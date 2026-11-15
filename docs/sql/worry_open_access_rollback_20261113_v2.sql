-- v2 전용: 원래 설치 함수 78308b5c52f3488896a078085b6679ec로 복원한다.
-- 원래 함수의 auth SELECT 의존성/별도 승인이 다시 생긴다. 정상 이용 복구용은 아니다.
-- 복구 전 우냥이만 비활성화한다. 이 파일은 적용용이 아니라 함수 복구 전용이다.
-- 기존 설치 원본과 사전조회 hash가 일치했던 경우에만 사용. 테이블/데이터/Cron 보존.
begin;
set local lock_timeout='5s';
set local statement_timeout='60s';
do $check$
begin
    if (select md5(replace(prosrc,chr(13),'')) from pg_proc
        where oid='public.worry_command(uuid,text,uuid,jsonb)'::regprocedure) is distinct from '21a2f59d82ed3537e7229e851aafcb44' then
        raise exception 'Unexpected function: use reviewed preflight backup instead';
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
    if not exists(select 1 from auth.users u join public.worry_access_grants g on g.user_id=u.id
        where u.id=p_owner and u.is_anonymous is false and u.role='authenticated' and u.deleted_at is null
        and g.child_eligibility_verified and g.notice_version='worry-v1-proposal'
        and g.consent_verified_at <= t and g.valid_until > t and g.revoked_at is null) then
        return jsonb_build_object('error','WORRY_ACCESS_REQUIRED');
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
        if p_args->>'notice_version' is distinct from 'worry-v1-proposal' then
            return jsonb_build_object('error','WORRY_NOTICE_REQUIRED'); end if;
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
            values(p_owner,p_args->>'key',p_args->>'notice_version',t,t+interval '168 hours') returning * into s;
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
do $verify$
begin
    if (select md5(replace(prosrc,chr(13),'')) from pg_proc
        where oid='public.worry_command(uuid,text,uuid,jsonb)'::regprocedure) is distinct from '78308b5c52f3488896a078085b6679ec' then
        raise exception 'Restored function hash mismatch: rollback';
    end if;
end;
$verify$;
commit;
