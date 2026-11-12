-- 검토용 신규 후보. 운영 미적용. 001~004/005 및 기존 일기·만화 테이블과 독립적이다.
-- 동의/아동 자격의 검증 절차와 보관 정책 승인 전 적용/활성화 금지.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

-- 신뢰된 운영 절차만 작성한다. FE Boolean이나 auth.user_metadata를 근거로 만들지 않는다.
create table public.worry_access_grants (
    user_id uuid primary key references auth.users(id) on delete cascade,
    notice_version text not null check (notice_version = 'worry-v1-proposal'),
    evidence_ref text not null check (length(btrim(evidence_ref)) between 1 and 200),
    child_eligibility_verified boolean not null check (child_eligibility_verified),
    consent_verified_at timestamptz not null,
    valid_until timestamptz not null,
    revoked_at timestamptz,
    check (valid_until > consent_verified_at)
);
create table public.worry_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    start_key text not null check (start_key ~ '^[A-Za-z0-9_-]{1,128}$'),
    notice_version text not null check (notice_version = 'worry-v1-proposal'),
    state text not null default 'active' check (state in ('active','finished')),
    created_at timestamptz not null,
    expires_at timestamptz not null,
    last_ai_status text check (last_ai_status in ('opening','reply','needs_adult','unavailable','finished')),
    unique (user_id,start_key),
    check (expires_at = created_at + interval '168 hours')
);
create unique index worry_one_active on public.worry_sessions(user_id) where state='active';
create index worry_expiry on public.worry_sessions(expires_at,id);
create index worry_owner_history on public.worry_sessions(user_id,created_at desc,id desc);
create table public.worry_messages (
    session_id uuid not null references public.worry_sessions(id) on delete cascade,
    position integer not null check (position between 0 and 24),
    role text not null check (role in ('child','assistant')),
    text text not null check (length(text) between 1 and 2000 and text ~ '[^[:space:]]'),
    created_at timestamptz not null,
    primary key (session_id,position)
);
create table public.worry_requests (
    id uuid primary key default gen_random_uuid(),
    session_id uuid not null references public.worry_sessions(id) on delete cascade,
    kind text not null check (kind in ('opening','message')),
    client_message_id text check (client_message_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    fingerprint text,
    status text not null default 'accepted'
        check (status in ('accepted','dispatched','completed','unavailable','rejected','unknown')),
    token uuid,
    deadline timestamptz not null,
    error_code text,
    mock boolean,
    created_at timestamptz not null,
    check ((kind='opening' and client_message_id is null and fingerprint is null)
        or (kind='message' and client_message_id is not null and fingerprint ~ '^[0-9a-f]{64}$')),
    unique (session_id,client_message_id)
);
create unique index worry_one_opening on public.worry_requests(session_id) where kind='opening';
alter table public.worry_access_grants enable row level security;
alter table public.worry_sessions enable row level security;
alter table public.worry_messages enable row level security;
alter table public.worry_requests enable row level security;
-- 사용자/보호자 직접 REST 접근 모두 차단. 서버 RPC에서 인증된 회원 소유권을 재검사한다.
revoke all on public.worry_access_grants, public.worry_sessions, public.worry_messages,
    public.worry_requests from public, anon, authenticated, service_role;
grant select on public.worry_access_grants to service_role;
grant select,insert,update,delete on public.worry_sessions,public.worry_messages,public.worry_requests to service_role;

create function public.worry_snapshot(p_session uuid) returns jsonb
language sql stable security invoker set search_path = '' as $fn$
    select jsonb_build_object('id',s.id,'state',s.state,'created_at',s.created_at,
        'expires_at',s.expires_at,'last_ai_status',s.last_ai_status,
        'messages',coalesce((select jsonb_agg(to_jsonb(m) order by m.position)
            from public.worry_messages m where m.session_id=s.id),'[]'::jsonb),
        'requests',coalesce((select jsonb_agg(jsonb_build_object(
            'id',r.id,'kind',r.kind,'client_message_id',r.client_message_id,
            'status',case when r.status in ('accepted','dispatched') and r.deadline <= clock_timestamp()
                          then 'unknown' else r.status end,
            'error_code',case when r.status in ('accepted','dispatched') and r.deadline <= clock_timestamp()
                          then 'WORRY_AI_OUTCOME_UNKNOWN' else r.error_code end,
            'mock',r.mock,'created_at',r.created_at) order by r.created_at,r.id)
            from public.worry_requests r where r.session_id=s.id),'[]'::jsonb))
    from public.worry_sessions s where s.id=p_session and s.expires_at > clock_timestamp();
$fn$;

create function public.worry_command(p_owner uuid,p_action text,p_session uuid,p_args jsonb)
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

-- BE 상시 실행과 독립적인 배치. 부모 세션만 삭제하면 메시지/요청 원장도 cascade된다.
create function public.purge_expired_worry_sessions(p_limit integer default 1000)
returns integer language plpgsql security invoker set search_path = '' as $fn$
declare n integer;
begin
    with doomed as (select id from public.worry_sessions where expires_at<=clock_timestamp()
        order by expires_at,id limit least(greatest(p_limit,1),10000) for update skip locked)
    delete from public.worry_sessions s using doomed d where s.id=d.id;
    get diagnostics n = row_count;
    return n;
end;
$fn$;
revoke all on function public.worry_snapshot(uuid), public.worry_command(uuid,text,uuid,jsonb),
    public.purge_expired_worry_sessions(integer) from public,anon,authenticated;
grant execute on function public.worry_snapshot(uuid), public.worry_command(uuid,text,uuid,jsonb),
    public.purge_expired_worry_sessions(integer) to service_role;
commit;
