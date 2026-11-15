-- 2026-11-13 v2 (auth SELECT 불필요): 이미 설치된 worry_conversations_v1의 함수 1개만 교체한다.
-- 테이블/컬럼/제약/기존 행/권한/purge/Cron 변경 없음. Migration005/설치 SQL 재실행 금지.
-- worry_access_grants는 보존하되 더 이상 이용 판단에 사용하지 않는다.
-- notice_version은 서버 안내 버전이며 동의·승인 증빙이 아니다. 기존 NOT NULL/CHECK 유지.
-- 정상 로그인 인증은 BE require_member -> AuthClient, SQL은 소유권·만료를 검사한다. 회원 상태는 Auth 서버 응답을 신뢰한다.
-- 아래 사전조회가 예상과 다르면 중단. 적용 부분 실패 시 ROLLBACK; 실행 후 원인 확인.
-- 1. 운영 적용 전 이 READ ONLY 블록만 선택 실행한다. rollback_sql 결과를 별도 파일로 저장한다.
begin transaction read only;
select md5(replace(prosrc,chr(13),'')) as function_hash,
       md5(replace(prosrc,chr(13),'')) in ('78308b5c52f3488896a078085b6679ec','21a2f59d82ed3537e7229e851aafcb44') as expected_definition,
       not prosecdef as security_invoker, proconfig, proacl,
       pg_get_functiondef(oid) as rollback_sql
from pg_proc where oid='public.worry_command(uuid,text,uuid,jsonb)'::regprocedure;
select r as role, has_function_privilege(r,'public.worry_command(uuid,text,uuid,jsonb)','EXECUTE') as can_execute
from unnest(array['anon','authenticated','service_role']) r;
-- 관찰값일 뿐 적용 전제 조건이 아니다. false여도 정상이며 GRANT하지 않는다.
select has_schema_privilege('service_role','auth','USAGE') as auth_usage,
       has_table_privilege('service_role','auth.users','SELECT') as auth_users_select;
select attnotnull, format_type(atttypid,atttypmod) as type
from pg_attribute where attrelid='public.worry_sessions'::regclass and attname='notice_version';
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint where conrelid='public.worry_sessions'::regclass;
select jobid,jobname,schedule,active,username,command from cron.job
where jobname='worry-expiry-minute' or command ilike '%purge_expired_worry_sessions%';
commit;

-- 2. 사전조회 확인 및 rollback_sql 저장 후 이 블록 전체를 실행한다.
begin;
set local lock_timeout='5s';
set local statement_timeout='60s';
do $check$
begin
    if not exists(select 1 from pg_proc where oid='public.worry_command(uuid,text,uuid,jsonb)'::regprocedure
        and md5(replace(prosrc,chr(13),'')) in ('78308b5c52f3488896a078085b6679ec','21a2f59d82ed3537e7229e851aafcb44')
        and not prosecdef and proconfig @> array['search_path=""']) then
        raise exception 'Unexpected worry_command definition: stop and review; no changes applied';
    end if;
    if has_function_privilege('anon','public.worry_command(uuid,text,uuid,jsonb)','EXECUTE')
       or has_function_privilege('authenticated','public.worry_command(uuid,text,uuid,jsonb)','EXECUTE')
       or not has_function_privilege('service_role','public.worry_command(uuid,text,uuid,jsonb)','EXECUTE') then
        raise exception 'Unexpected RPC ACL: do not open RPC to browser roles';
    end if;
    -- auth.users SELECT/스키마 USAGE는 이 RPC의 실행 전제 조건이 아니다. 권한을 추가하지 않는다.
    if exists(select 1 from pg_class where oid in ('public.worry_sessions'::regclass,
        'public.worry_messages'::regclass,'public.worry_requests'::regclass) and not relrowsecurity) then
        raise exception 'Worry RLS must remain enabled';
    end if;
end;
$check$;
create or replace function public.worry_command(p_owner uuid,p_action text,p_session uuid,p_args jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $fn$
declare
    s public.worry_sessions%rowtype; r public.worry_requests%rowtype;
    t timestamptz; n integer; total integer; fp text; reply jsonb; outcome text; err text;
begin
    -- 같은 회원의 start/send/finish를 여러 프로세스에서도 직렬화한다. 모델 호출 중에는 잠그지 않는다.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_owner::text, 73));
    t := clock_timestamp();
    -- p_owner는 BE의 검증된 AuthClient UUID만 받는다. anon/authenticated RPC 실행은 계속 금지한다.
    -- 회원 확인은 요청마다 /auth/v1/user에서 수행한다. SQL에서는 직접 auth 조회를 하지 않는다.
    if p_owner is null or p_owner='00000000-0000-0000-0000-000000000000'::uuid then
        return jsonb_build_object('error','WORRY_MEMBER_REQUIRED');
    end if;
    if p_action='list' then
        return jsonb_build_object('sessions',coalesce((select jsonb_agg(q.item order by q.created_at desc,q.id desc) from (
            select s0.id,s0.created_at,jsonb_build_object('id',s0.id,'state',s0.state,
                'created_at',s0.created_at,'expires_at',s0.expires_at,'last_ai_status',s0.last_ai_status) item
            from public.worry_sessions s0 where s0.user_id=p_owner and s0.expires_at>t
            and exists(select 1 from public.worry_messages m where m.session_id=s0.id and m.role='child')
            order by s0.created_at desc,s0.id desc
            limit least(greatest(coalesce((p_args->>'limit')::integer,50),1),100)
            offset least(greatest(coalesce((p_args->>'offset')::integer,0),0),10000)
        ) q),'[]'::jsonb));
    end if;
    if p_action='start' then
        select * into s from public.worry_sessions where user_id=p_owner and start_key=p_args->>'key';
        if found then
            if s.expires_at <= t then return jsonb_build_object('error','WORRY_NOT_FOUND'); end if;
            return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',false);
        end if;
        update public.worry_requests q set status='unknown',error_code='WORRY_AI_OUTCOME_UNKNOWN'
            from public.worry_sessions old where old.id=q.session_id and old.user_id=p_owner
            and old.state='active' and q.status in ('accepted','dispatched');
        update public.worry_sessions set state='finished' where user_id=p_owner and state='active';
        insert into public.worry_sessions(user_id,start_key,notice_version,created_at,expires_at)
            values(p_owner,p_args->>'key','worry-v1-proposal',t,t+interval '168 hours') returning * into s;
        insert into public.worry_requests(session_id,kind,created_at,deadline)
            values(s.id,'opening',t,t+interval '60 seconds') returning * into r;
        return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',true,'request_id',r.id);
    end if;
    select * into s from public.worry_sessions where id=p_session and user_id=p_owner and expires_at>t for update;
    if not found then return jsonb_build_object('error','WORRY_NOT_FOUND'); end if;
    if p_action='get' then return jsonb_build_object('session',public.worry_snapshot(s.id)); end if;
    if p_action='finish' then
        update public.worry_sessions set state='finished' where id=s.id;
        update public.worry_requests set status='unknown',error_code='WORRY_AI_OUTCOME_UNKNOWN'
            where session_id=s.id and status in ('accepted','dispatched');
        return jsonb_build_object('session',public.worry_snapshot(s.id));
    end if;
    if p_action='admit' then
        fp := encode(sha256(convert_to(p_args->>'text','UTF8')),'hex');
        select * into r from public.worry_requests where session_id=s.id and client_message_id=p_args->>'key';
        if found then
            if r.fingerprint is distinct from fp then return jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); end if;
            return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',false);
        end if;
        if s.state <> 'active' then return jsonb_build_object('error','WORRY_FINISHED'); end if;
        if exists(select 1 from public.worry_requests where session_id=s.id and status <> 'completed') then
            return jsonb_build_object('error','WORRY_RECOVERY_REQUIRED'); end if;
        select count(*),coalesce(sum(length(text)),0) into n,total from public.worry_messages where session_id=s.id;
        if n+1>24 or total+length(p_args->>'text')>8000 then
            return jsonb_build_object('error','WORRY_HISTORY_LIMIT'); end if;
        insert into public.worry_messages values(s.id,n,'child',p_args->>'text',t);
        insert into public.worry_requests(session_id,kind,client_message_id,fingerprint,created_at,deadline)
            values(s.id,'message',p_args->>'key',fp,t,t+interval '60 seconds') returning * into r;
        return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',true,'request_id',r.id);
    end if;
    select * into r from public.worry_requests where id=(p_args->>'request_id')::uuid and session_id=s.id for update;
    if not found then return jsonb_build_object('error','WORRY_NOT_FOUND'); end if;
    if p_action='dispatch' then
        if s.state<>'active' or r.status<>'accepted' or r.deadline<=t then
            return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',false); end if;
        if p_args->>'token' is null then raise exception 'token required'; end if;
        update public.worry_requests set status='dispatched',token=(p_args->>'token')::uuid,
            deadline=t+interval '60 seconds' where id=r.id;
        return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',true);
    end if;
    if p_action='complete' then
        -- 종료/새 세션/만료/소유권 전이 후 도착한 응답은 저장하거나 대화를 다시 열지 않는다.
        if s.state<>'active' or r.status<>'dispatched' or r.deadline<=t
            or r.token is distinct from (p_args->>'token')::uuid then
            return jsonb_build_object('session',public.worry_snapshot(s.id),'execute',false); end if;
        reply := p_args->'reply'; err := p_args->>'error_code';
        if reply is not null and reply <> 'null'::jsonb then
            if coalesce(reply->>'status','') not in ('opening','reply','needs_adult','unavailable','finished')
                or jsonb_typeof(reply->'mock') is distinct from 'boolean' then raise exception 'invalid reply'; end if;
            select count(*) into n from public.worry_messages where session_id=s.id;
            insert into public.worry_messages values(s.id,n,'assistant',reply->>'message',t);
            outcome := case when reply->>'status'='unavailable' then 'unavailable' else 'completed' end;
            update public.worry_sessions set last_ai_status=reply->>'status',
                state=case when reply->>'status' in ('needs_adult','finished') then 'finished' else state end where id=s.id;
        else
            outcome := case err when 'WORRY_AI_REJECTED' then 'rejected'
                when 'WORRY_AI_NOT_SENT' then 'unavailable' else 'unknown' end;
            err := case when err in ('WORRY_AI_REJECTED','WORRY_AI_NOT_SENT') then err else 'WORRY_AI_OUTCOME_UNKNOWN' end;
        end if;
        update public.worry_requests set status=outcome,error_code=err,mock=(reply->>'mock')::boolean where id=r.id;
        return jsonb_build_object('session',public.worry_snapshot(s.id));
    end if;
    raise exception 'invalid action';
end;
$fn$;
-- CREATE OR REPLACE는 기존 함수 소유자와 ACL을 보존한다. GRANT는 수행하지 않는다.
do $verify$
begin
    if (select md5(replace(prosrc,chr(13),'')) from pg_proc
        where oid='public.worry_command(uuid,text,uuid,jsonb)'::regprocedure) is distinct from '21a2f59d82ed3537e7229e851aafcb44' then
        raise exception 'Installed function hash mismatch: rollback';
    end if;
end;
$verify$;
select md5(replace(prosrc,chr(13),''))='21a2f59d82ed3537e7229e851aafcb44' as open_access_installed
from pg_proc where oid='public.worry_command(uuid,text,uuid,jsonb)'::regprocedure;
commit;
