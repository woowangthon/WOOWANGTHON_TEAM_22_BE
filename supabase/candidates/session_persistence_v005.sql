-- 로컬 검토 후보. 원격 미적용·SQL 미실행. 001~004 및 기존 회원 데이터는 변경하지 않습니다.
-- 자동 삭제/백필 없음. DB 적용·권한·실잠금 검증 전 SESSION_STORE_MODE=supabase 활성화 금지.
create table public.chat_sessions (
    session_id text primary key check (session_id ~ '^ses_[0-9a-f]{32}$'),
    owner_id uuid not null references auth.users(id) on update restrict on delete restrict,
    schema_version integer not null default 1 check (schema_version = 1),
    messages jsonb not null default '[]' check (pg_catalog.jsonb_typeof(messages) = 'array'),
    history_revision integer not null default 0 check (history_revision between 0 and 29),
    version bigint not null default 0 check (version >= 0),
    lifecycle text not null default 'starting' check (lifecycle in ('starting','active','confirmed','expired')),
    active_request_id uuid,
    confirmed_diary_id uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    expires_at timestamptz not null default (now() + interval '7 days'),
    unique(session_id, owner_id),
    foreign key (confirmed_diary_id, owner_id) references public.diaries(id, user_id)
        on update restrict on delete restrict,
    check (pg_catalog.jsonb_array_length(messages) <= 60),
    check (pg_catalog.octet_length(messages::text) <= 524288),
    check ((lifecycle = 'confirmed') = (confirmed_diary_id is not null)),
    check ((lifecycle = 'starting' and messages = '[]'::jsonb and history_revision = 0)
        or (lifecycle <> 'starting' and pg_catalog.jsonb_array_length(messages) = 2 * history_revision + 1))
);

create table public.chat_requests (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null,
    session_id text not null,
    kind text not null check (kind in ('start','message','draft','confirm')),
    client_key text not null check (client_key ~ '^[A-Za-z0-9_-]{1,128}$'),
    fingerprint text not null,
    input jsonb not null check (pg_catalog.jsonb_typeof(input) = 'object'),
    base_revision integer not null check (base_revision between 0 and 29),
    state text not null check (state in ('reserved','dispatched','succeeded','retryable_before_send','failed','unknown')),
    attempt_token uuid not null,
    lease_until timestamptz not null,
    dispatched_deadline timestamptz,
    result jsonb,
    error jsonb,
    confirmed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique(id, session_id, owner_id),
    unique(owner_id, session_id, kind, client_key),
    foreign key (session_id, owner_id) references public.chat_sessions(session_id, owner_id)
        on update restrict on delete restrict,
    check ((kind = 'start' and fingerprint = '')
        or (kind = 'draft' and fingerprint ~ '^[0-9]+$')
        or (kind in ('message','confirm') and fingerprint ~ '^[0-9a-f]{64}$')),
    check (pg_catalog.octet_length(input::text) <= 524288
        and coalesce(pg_catalog.octet_length(result::text),0) <= 1048576),
    check ((state = 'succeeded' and result is not null and pg_catalog.jsonb_typeof(result) = 'object' and error is null)
        or (state in ('reserved','dispatched') and result is null and error is null)
        or (state in ('retryable_before_send','failed','unknown') and result is null
            and error is not null and pg_catalog.jsonb_typeof(error) = 'object')),
    check ((kind = 'confirm') = (confirmed_at is not null)),
    check (kind <> 'confirm' or state in ('reserved','succeeded','failed')),
    check (state <> 'dispatched' or dispatched_deadline is not null)
);
create unique index chat_requests_start_key_uq on public.chat_requests(owner_id, client_key) where kind = 'start';
create unique index chat_requests_confirm_key_uq on public.chat_requests(owner_id, client_key) where kind = 'confirm';
create unique index chat_requests_draft_id_uq on public.chat_requests((result->'draft'->>'draft_id'))
    where kind = 'draft' and state = 'succeeded';
create index chat_requests_session_idx on public.chat_requests(session_id, owner_id, state);
create index chat_sessions_owner_idx on public.chat_sessions(owner_id, lifecycle);
alter table public.chat_sessions add constraint chat_sessions_active_request_fk
    foreign key (active_request_id, session_id, owner_id)
    references public.chat_requests(id, session_id, owner_id) on update restrict on delete restrict;

alter table public.chat_sessions enable row level security;
alter table public.chat_requests enable row level security;
-- 회원 직접 읽기/쓰기도 허용하지 않습니다. service_role은 RLS 우회하므로 RPC 소유권 검사가 필수입니다.
revoke all on public.chat_sessions, public.chat_requests from public, anon, authenticated, service_role;
grant select, insert on public.chat_sessions, public.chat_requests to service_role;
grant update(messages, history_revision, version, lifecycle, active_request_id,
    confirmed_diary_id, updated_at, expires_at) on public.chat_sessions to service_role;
grant update(state, attempt_token, lease_until, dispatched_deadline, result, error, updated_at)
    on public.chat_requests to service_role;

create function public.reserve_chat_request(
    p_owner_id uuid, p_session_id text, p_kind text, p_client_key text,
    p_fingerprint text, p_input jsonb, p_attempt_token uuid
) returns jsonb language plpgsql security invoker set search_path = '' as $function$
declare
    s public.chat_sessions%rowtype;
    r public.chat_requests%rowtype;
    a public.chat_requests%rowtype;
    v_now timestamptz;
    v_chars bigint;
    v_size bigint;
begin
    if p_owner_id is null or not exists(select 1 from auth.users u where u.id = p_owner_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    if p_session_id is null or p_session_id !~ '^ses_[0-9a-f]{32}$'
        or p_kind is null or p_kind not in ('start','message','draft','confirm')
        or p_client_key is null or p_client_key !~ '^[A-Za-z0-9_-]{1,128}$'
        or p_fingerprint is null or p_attempt_token is null
        or p_input is null or pg_catalog.jsonb_typeof(p_input) <> 'object'
        or pg_catalog.octet_length(p_input::text) > 524288 then
        raise exception using errcode = '22023', message = 'invalid reservation';
    end if;
    if (p_kind = 'start' and (p_input <> '{}'::jsonb or p_fingerprint <> ''))
        or (p_kind = 'message' and not coalesce(
            (p_input - 'text') = '{}'::jsonb and pg_catalog.jsonb_typeof(p_input->'text') = 'string'
            and pg_catalog.char_length(p_input->>'text') between 1 and 2000
            and (p_input->>'text') ~ '[^[:space:]]' and p_fingerprint ~ '^[0-9a-f]{64}$', false))
        or (p_kind = 'draft' and not coalesce(
            (p_input - 'history_revision') = '{}'::jsonb
            and pg_catalog.jsonb_typeof(p_input->'history_revision') = 'number'
            and p_input->>'history_revision' ~ '^[0-9]+$'
            and p_fingerprint = p_input->>'history_revision', false))
        or (p_kind = 'confirm' and not coalesce(
            (p_input - array['client_request_id','history_revision','draft_id','weather']) = '{}'::jsonb
            and p_input->>'client_request_id' = p_client_key
            and pg_catalog.jsonb_typeof(p_input->'history_revision') = 'number'
            and p_input->>'history_revision' ~ '^[0-9]+$'
            and p_input->>'draft_id' ~ '^draft_[0-9a-f]{32}$'
            and p_input ? 'weather'
            and (p_input->'weather' = 'null'::jsonb or p_input->>'weather' in ('sunny','cloudy','snowy','rainy'))
            and p_fingerprint ~ '^[0-9a-f]{64}$', false)) then
        raise exception using errcode = '22023', message = 'invalid input';
    end if;
    -- 003과 같은 회원 잠금이 필요할 때는 session/request보다 먼저 취득합니다.
    if p_kind in ('start','confirm') then
        perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_owner_id::text, 0));
    end if;
    if p_kind = 'start' then
        select q.* into r from public.chat_requests q
            where q.owner_id = p_owner_id and q.kind = 'start' and q.client_key = p_client_key;
        if r.id is null then
            if (select count(*) from public.chat_sessions c where c.owner_id = p_owner_id
                and c.lifecycle in ('starting','active')) >= 3 then
                return pg_catalog.jsonb_build_object('error','SESSION_LIMIT_REACHED');
            end if;
            insert into public.chat_sessions(session_id, owner_id) values(p_session_id, p_owner_id);
        else
            p_session_id := r.session_id;
        end if;
    end if;
    select c.* into s from public.chat_sessions c
        where c.session_id = p_session_id and c.owner_id = p_owner_id for update;
    if not found then return pg_catalog.jsonb_build_object('error','SESSION_NOT_FOUND'); end if;
    if s.schema_version <> 1 then raise exception using errcode = '22023', message = 'invalid schema'; end if;
    select q.* into r from public.chat_requests q where q.owner_id = p_owner_id and q.kind = p_kind
        and q.client_key = p_client_key and (p_kind in ('start','confirm') or q.session_id = p_session_id) for update;
    if r.id is not null and (r.fingerprint <> p_fingerprint or r.session_id <> p_session_id or r.input <> p_input) then
        return pg_catalog.jsonb_build_object('error','IDEMPOTENCY_CONFLICT');
    end if;
    v_now := pg_catalog.clock_timestamp();
    if s.active_request_id is not null then
        select q.* into a from public.chat_requests q where q.id = s.active_request_id for update;
        if a.state = 'dispatched' and a.dispatched_deadline <= v_now then
            update public.chat_requests set state = 'unknown', updated_at = v_now,
                error = '{"status":502,"code":"AI_OUTCOME_UNKNOWN","message":"응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.","retryable":false}'::jsonb
                where id = a.id returning * into a;
            update public.chat_sessions set active_request_id = null, version = version + 1,
                updated_at = v_now where session_id = s.session_id returning * into s;
            if r.id = a.id then r := a; end if;
        end if;
    end if;
    -- 성공/최종 오류는 현재 revision이 바뀌어도 당시 응답을 그대로 반환합니다.
    if r.id is not null and r.state in ('succeeded','failed','unknown') then
        return pg_catalog.jsonb_build_object('session',pg_catalog.to_jsonb(s),'request',pg_catalog.to_jsonb(r),'acquired',false);
    end if;
    if exists(select 1 from public.chat_requests q where q.session_id = s.session_id
        and q.owner_id = p_owner_id and q.state = 'unknown') then
        return pg_catalog.jsonb_build_object('error','AI_OUTCOME_UNKNOWN');
    end if;
    if s.lifecycle = 'confirmed' then return pg_catalog.jsonb_build_object('error','SESSION_ALREADY_CONFIRMED'); end if;
    if r.id is not null and (r.state = 'dispatched'
        or (r.state = 'reserved' and r.lease_until > v_now and p_kind <> 'confirm')) then
        return pg_catalog.jsonb_build_object('session',pg_catalog.to_jsonb(s),'request',pg_catalog.to_jsonb(r),'acquired',false);
    end if;
    if s.active_request_id is not null and s.active_request_id is distinct from r.id then
        return pg_catalog.jsonb_build_object('error','SESSION_BUSY');
    end if;
    if r.id is not null and r.base_revision <> s.history_revision then
        return pg_catalog.jsonb_build_object('error','HISTORY_REVISION_CONFLICT');
    end if;
    if p_kind in ('draft','confirm') and (p_input->>'history_revision')::numeric <> s.history_revision then
        return pg_catalog.jsonb_build_object('error','HISTORY_REVISION_CONFLICT');
    end if;
    if p_kind <> 'start' and s.lifecycle <> 'active' then
        return pg_catalog.jsonb_build_object('error','SESSION_BUSY');
    end if;
    if p_kind in ('draft','confirm') and s.history_revision < 1 then
        return pg_catalog.jsonb_build_object('error','NO_CHILD_MESSAGES');
    end if;
    if p_kind = 'confirm' and not exists(select 1 from public.chat_requests q
        where q.owner_id = p_owner_id and q.session_id = p_session_id and q.kind = 'draft'
        and q.state = 'succeeded' and q.base_revision = s.history_revision
        and q.result->'draft'->>'draft_id' = p_input->>'draft_id') then
        return pg_catalog.jsonb_build_object('error','DIARY_DRAFT_NOT_FOUND');
    end if;
    select coalesce(sum(pg_catalog.char_length(m.value->>'text')),0) into v_chars
        from pg_catalog.jsonb_array_elements(s.messages) m(value);
    if p_kind = 'message' and (pg_catalog.jsonb_array_length(s.messages) + 2 > 60
        or v_chars + pg_catalog.char_length(p_input->>'text') + 150 > 20000) then
        return pg_catalog.jsonb_build_object('error','CONVERSATION_LIMIT_REACHED');
    end if;
    select coalesce(sum(pg_catalog.octet_length(q.input::text) + coalesce(pg_catalog.octet_length(q.result::text),0)),0)
        + pg_catalog.octet_length(s.messages::text) into v_size
        from public.chat_requests q where q.session_id = p_session_id;
    if r.id is null and ((select count(*) from public.chat_requests q where q.session_id = p_session_id) >= 128
        or v_size + pg_catalog.octet_length(p_input::text) > 2097152) then
        return pg_catalog.jsonb_build_object('error','SESSION_LIMIT_REACHED');
    end if;
    if r.id is null then
        insert into public.chat_requests(owner_id,session_id,kind,client_key,fingerprint,input,
            base_revision,state,attempt_token,lease_until,confirmed_at)
        values(p_owner_id,p_session_id,p_kind,p_client_key,p_fingerprint,p_input,s.history_revision,
            'reserved',p_attempt_token,v_now + interval '30 seconds',case when p_kind = 'confirm' then v_now end)
        returning * into r;
    else
        -- 만료 예약/연결 전 실패만 같은 원문으로 인계합니다. confirmed_at은 바꾸지 않습니다.
        update public.chat_requests set state = 'reserved', attempt_token = p_attempt_token,
            lease_until = v_now + interval '30 seconds', dispatched_deadline = null,
            error = null, updated_at = v_now where id = r.id returning * into r;
    end if;
    update public.chat_sessions set active_request_id = r.id, version = version + 1,
        updated_at = v_now where session_id = s.session_id returning * into s;
    return pg_catalog.jsonb_build_object('session',pg_catalog.to_jsonb(s),'request',pg_catalog.to_jsonb(r),'acquired',true);
end;
$function$;

create function public.transition_chat_request(
    p_owner_id uuid, p_session_id text, p_request_id uuid, p_attempt_token uuid,
    p_version bigint, p_action text, p_result jsonb, p_error jsonb, p_dispatch_seconds integer
) returns jsonb language plpgsql security invoker set search_path = '' as $function$
declare
    s public.chat_sessions%rowtype;
    r public.chat_requests%rowtype;
    v_now timestamptz;
    v_messages jsonb;
    v_revision integer;
    v_expected jsonb;
    v_size bigint;
begin
    if p_owner_id is null or not exists(select 1 from auth.users u where u.id = p_owner_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    select c.* into s from public.chat_sessions c where c.session_id = p_session_id and c.owner_id = p_owner_id for update;
    if not found then return pg_catalog.jsonb_build_object('error','SESSION_NOT_FOUND'); end if;
    select q.* into r from public.chat_requests q where q.id = p_request_id
        and q.owner_id = p_owner_id and q.session_id = p_session_id for update;
    if not found or r.kind = 'confirm' or p_attempt_token is null or r.attempt_token <> p_attempt_token
        or p_version is null or s.version <> p_version or s.active_request_id is distinct from r.id
        or r.base_revision <> s.history_revision then
        return pg_catalog.jsonb_build_object('error','REQUEST_IN_PROGRESS');
    end if;
    v_now := pg_catalog.clock_timestamp();
    if r.state = 'dispatched' and r.dispatched_deadline <= v_now then
        update public.chat_requests set state = 'unknown', updated_at = v_now,
            error = '{"status":502,"code":"AI_OUTCOME_UNKNOWN","message":"응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.","retryable":false}'::jsonb
            where id = r.id;
        update public.chat_sessions set active_request_id = null,version = version + 1,updated_at = v_now
            where session_id = s.session_id;
        return pg_catalog.jsonb_build_object('error','AI_OUTCOME_UNKNOWN');
    end if;
    if p_action = 'dispatched' then
        if r.state <> 'reserved' or r.lease_until <= v_now then
            return pg_catalog.jsonb_build_object('error','REQUEST_IN_PROGRESS');
        end if;
        if p_dispatch_seconds is null or p_dispatch_seconds not between 1 and 86400
            or p_result is not null or p_error is not null then
            raise exception using errcode = '22023', message = 'invalid dispatch';
        end if;
        update public.chat_requests set state = 'dispatched',
            dispatched_deadline = v_now + pg_catalog.make_interval(secs => p_dispatch_seconds), updated_at = v_now
            where id = r.id returning * into r;
    elsif r.state <> 'dispatched' then
        return pg_catalog.jsonb_build_object('error','REQUEST_IN_PROGRESS');
    elsif p_action = 'succeeded' then
        if p_error is not null or not coalesce(pg_catalog.jsonb_typeof(p_result) = 'object'
            and pg_catalog.octet_length(p_result::text) <= 1048576
            and p_result->>'session_id' = p_session_id
            and pg_catalog.jsonb_typeof(p_result->'history_revision') = 'number',false) then
            raise exception using errcode = '22023', message = 'invalid result';
        end if;
        v_messages := s.messages;
        v_revision := s.history_revision;
        if r.kind in ('start','message') then
            if not coalesce(pg_catalog.jsonb_typeof(p_result->'reply') = 'object'
                and p_result->'reply'->>'message_id' ~ '^msg_[0-9a-f]{32}$'
                and p_result->'reply'->>'role' = 'assistant'
                and pg_catalog.jsonb_typeof(p_result->'reply'->'text') = 'string'
                and pg_catalog.char_length(p_result->'reply'->>'text') between 1 and 150
                and p_result->'reply'->>'text' ~ '[^[:space:]]'
                and pg_catalog.jsonb_typeof(p_result->'finished') = 'boolean'
                and pg_catalog.jsonb_typeof(p_result->'mock') = 'boolean'
                and pg_catalog.jsonb_typeof(p_result->'fallback') = 'boolean',false) then
                raise exception using errcode = '22023', message = 'invalid reply';
            end if;
            if r.kind = 'start' then
                if s.lifecycle <> 'starting' then raise exception using errcode = '22023', message = 'invalid start'; end if;
                v_messages := pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('role','assistant','text',p_result->'reply'->>'text'));
            else
                if p_result->>'client_message_id' is distinct from r.client_key then
                    raise exception using errcode = '22023', message = 'invalid message key';
                end if;
                v_messages := v_messages || pg_catalog.jsonb_build_array(
                    pg_catalog.jsonb_build_object('role','child','text',r.input->>'text'),
                    pg_catalog.jsonb_build_object('role','assistant','text',p_result->'reply'->>'text'));
                v_revision := v_revision + 1;
            end if;
        else
            if not coalesce(p_result->>'client_request_id' = r.client_key
                and pg_catalog.jsonb_typeof(p_result->'draft') = 'object'
                and p_result->'draft'->>'draft_id' ~ '^draft_[0-9a-f]{32}$'
                and p_result->'draft'->>'status' = 'draft'
                and pg_catalog.jsonb_typeof(p_result->'draft'->'text') = 'string'
                and pg_catalog.char_length(p_result->'draft'->>'text') between 1 and 20000
                and p_result->'draft'->>'text' ~ '[^[:space:]]'
                and p_result->'draft'->'requires_confirmation' = 'true'::jsonb
                and pg_catalog.jsonb_typeof(p_result->'draft'->'mock') = 'boolean'
                and pg_catalog.jsonb_typeof(p_result->'draft'->'fallback') = 'boolean'
                and pg_catalog.jsonb_typeof(p_result->'draft'->'sources') = 'array'
                and pg_catalog.jsonb_typeof(p_result->'draft'->'pages') = 'array',false) then
                raise exception using errcode = '22023', message = 'invalid draft';
            end if;
            select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('id',m.ordinal - 1,'text',m.value->>'text') order by m.ordinal)
                into v_expected from pg_catalog.jsonb_array_elements(s.messages) with ordinality m(value,ordinal)
                where m.value->>'role' = 'child';
            if p_result->'draft'->'sources' is distinct from v_expected
                or pg_catalog.jsonb_array_length(p_result->'draft'->'pages') not between 1 and 6 then
                raise exception using errcode = '22023', message = 'invalid draft sources';
            end if;
            if exists(select 1 from pg_catalog.jsonb_array_elements(p_result->'draft'->'pages') p(value)
                where not coalesce(pg_catalog.jsonb_typeof(p.value->'text') = 'string'
                    and pg_catalog.char_length(p.value->>'text') between 1 and 20000
                    and p.value->>'text' ~ '[^[:space:]]'
                    and p.value->'imageUrl' = 'null'::jsonb
                    and pg_catalog.jsonb_typeof(p.value->'source_ids') = 'array',false)) then
                raise exception using errcode = '22023', message = 'invalid draft pages';
            end if;
            if exists(select 1 from pg_catalog.jsonb_array_elements(p_result->'draft'->'pages') p(value)
                where pg_catalog.jsonb_array_length(p.value->'source_ids') < 1
                or exists(select 1 from pg_catalog.jsonb_array_elements(p.value->'source_ids') i(value)
                    where not exists(select 1 from pg_catalog.jsonb_array_elements(v_expected) e(value)
                        where e.value->'id' = i.value))
                or (select count(*) <> count(distinct i.value) from pg_catalog.jsonb_array_elements(p.value->'source_ids') i(value))) then
                raise exception using errcode = '22023', message = 'invalid page sources';
            end if;
            -- 기존 AIClient의 Python split()과 같은 Unicode 공백 집합을 사용합니다.
            if pg_catalog.btrim(pg_catalog.regexp_replace(p_result->'draft'->>'text',
                    U&'[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+', ' ', 'g')) <>
                (select pg_catalog.btrim(pg_catalog.regexp_replace(pg_catalog.string_agg(p.value->>'text',' ' order by p.ordinal),
                    U&'[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+', ' ', 'g'))
                 from pg_catalog.jsonb_array_elements(p_result->'draft'->'pages') with ordinality p(value,ordinal)) then
                raise exception using errcode = '22023', message = 'invalid page text';
            end if;
        end if;
        if p_result->>'history_revision' is distinct from v_revision::text
            or pg_catalog.jsonb_array_length(v_messages) <> 2 * v_revision + 1
            or pg_catalog.jsonb_array_length(v_messages) > 60
            or exists(select 1 from pg_catalog.jsonb_array_elements(v_messages) with ordinality m(value,ordinal)
                where not coalesce(m.value->>'role' = case when m.ordinal % 2 = 1 then 'assistant' else 'child' end
                    and pg_catalog.jsonb_typeof(m.value->'text') = 'string'
                    and pg_catalog.char_length(m.value->>'text') between 1 and case when m.ordinal % 2 = 1 then 150 else 2000 end
                    and m.value->>'text' ~ '[^[:space:]]',false))
            or (select sum(pg_catalog.char_length(m.value->>'text')) from pg_catalog.jsonb_array_elements(v_messages) m(value)) > 20000 then
            raise exception using errcode = '22023', message = 'invalid snapshot';
        end if;
        select coalesce(sum(pg_catalog.octet_length(q.input::text) + coalesce(pg_catalog.octet_length(q.result::text),0)),0)
            + pg_catalog.octet_length(v_messages::text) + pg_catalog.octet_length(p_result::text) into v_size
            from public.chat_requests q where q.session_id = p_session_id;
        if v_size > 2097152 then
            -- 결과를 버린 뒤 재호출하지 않도록 unknown으로 남깁니다.
            update public.chat_requests set state = 'unknown',updated_at = v_now,
                error = '{"status":502,"code":"AI_OUTCOME_UNKNOWN","message":"응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.","retryable":false}'::jsonb where id = r.id;
            update public.chat_sessions set active_request_id = null,version = version + 1,updated_at = v_now where session_id = s.session_id;
            return pg_catalog.jsonb_build_object('error','AI_OUTCOME_UNKNOWN');
        end if;
        update public.chat_requests set state = 'succeeded',result = p_result,error = null,updated_at = v_now
            where id = r.id returning * into r;
        update public.chat_sessions set messages = v_messages,history_revision = v_revision,
            lifecycle = 'active',updated_at = v_now,expires_at = v_now + interval '7 days'
            where session_id = s.session_id returning * into s;
    elsif p_action in ('retryable_before_send','failed','unknown') then
        -- 공급자 본문/stack trace는 받지 않고 기존 안전한 오류만 허용합니다.
        if p_result is not null or not coalesce(pg_catalog.jsonb_typeof(p_error) = 'object'
            and (p_error - array['status','code','message','retryable']) = '{}'::jsonb
            and pg_catalog.jsonb_typeof(p_error->'status') = 'number'
            and p_error->>'status' in ('500','502','503','504')
            and pg_catalog.jsonb_typeof(p_error->'message') = 'string'
            and pg_catalog.char_length(p_error->>'message') between 1 and 200
            and ((p_action = 'retryable_before_send' and p_error->>'code' = 'AI_CONNECTION_FAILED' and p_error->'retryable' = 'true'::jsonb)
                or (p_action = 'unknown' and p_error->>'code' = 'AI_OUTCOME_UNKNOWN' and p_error->'retryable' = 'false'::jsonb)
                or (p_action = 'failed' and p_error->>'code' in ('AI_UNAVAILABLE','AI_BAD_RESPONSE','AI_REQUEST_REJECTED') and p_error->'retryable' = 'false'::jsonb)),false) then
            raise exception using errcode = '22023', message = 'invalid failure';
        end if;
        update public.chat_requests set state = p_action,error = p_error,updated_at = v_now
            where id = r.id returning * into r;
    else
        raise exception using errcode = '22023', message = 'invalid transition';
    end if;
    if p_action <> 'dispatched' then
        update public.chat_sessions set active_request_id = null,version = version + 1,updated_at = v_now
            where session_id = s.session_id returning * into s;
    end if;
    return pg_catalog.jsonb_build_object('session',pg_catalog.to_jsonb(s),'request',pg_catalog.to_jsonb(r),'acquired',false);
end;
$function$;

create function public.confirm_chat_session_diary(
    p_owner_id uuid, p_session_id text, p_request_id uuid, p_attempt_token uuid, p_version bigint
) returns jsonb language plpgsql security invoker set search_path = '' as $function$
declare
    s public.chat_sessions%rowtype;
    r public.chat_requests%rowtype;
    v_draft jsonb;
    v_messages jsonb;
    v_diary_id uuid;
    v_detail text;
begin
    if p_owner_id is null or not exists(select 1 from auth.users u where u.id = p_owner_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_owner_id::text, 0));
    select c.* into s from public.chat_sessions c where c.owner_id = p_owner_id and c.session_id = p_session_id for update;
    if not found then return pg_catalog.jsonb_build_object('error','SESSION_NOT_FOUND'); end if;
    select q.* into r from public.chat_requests q where q.id = p_request_id
        and q.owner_id = p_owner_id and q.session_id = p_session_id and q.kind = 'confirm' for update;
    if not found then return pg_catalog.jsonb_build_object('error','REQUEST_IN_PROGRESS'); end if;
    if r.state = 'succeeded' then
        return pg_catalog.jsonb_build_object('session',pg_catalog.to_jsonb(s),'request',pg_catalog.to_jsonb(r),'acquired',false);
    end if;
    if p_attempt_token is null or r.attempt_token <> p_attempt_token or p_version is null
        or s.version <> p_version or s.active_request_id is distinct from r.id or r.state <> 'reserved' then
        return pg_catalog.jsonb_build_object('error','REQUEST_IN_PROGRESS');
    end if;
    if exists(select 1 from public.chat_requests q where q.session_id = s.session_id
        and q.owner_id = p_owner_id and q.state = 'unknown') then
        return pg_catalog.jsonb_build_object('error','AI_OUTCOME_UNKNOWN');
    end if;
    if s.lifecycle = 'confirmed' then return pg_catalog.jsonb_build_object('error','SESSION_ALREADY_CONFIRMED'); end if;
    if s.history_revision <> r.base_revision or r.input->>'history_revision' <> s.history_revision::text then
        return pg_catalog.jsonb_build_object('error','HISTORY_REVISION_CONFLICT');
    end if;
    select q.result->'draft' into v_draft from public.chat_requests q where q.session_id = s.session_id
        and q.owner_id = p_owner_id and q.kind = 'draft' and q.state = 'succeeded'
        and q.base_revision = s.history_revision and q.result->'draft'->>'draft_id' = r.input->>'draft_id';
    if v_draft is null then return pg_catalog.jsonb_build_object('error','DIARY_DRAFT_NOT_FOUND'); end if;
    select pg_catalog.jsonb_agg(m.value || pg_catalog.jsonb_build_object('position',m.ordinal - 1) order by m.ordinal)
        into v_messages from pg_catalog.jsonb_array_elements(s.messages) with ordinality m(value,ordinal);
    -- 003의 날짜/회원별 요청 고유성·일기/원문 저장을 같은 트랜잭션 안에서 재사용합니다.
    begin
        v_diary_id := public.confirm_member_diary(p_owner_id,p_session_id,r.client_key,s.history_revision,
            r.fingerprint,r.input->>'weather',r.confirmed_at,v_draft,v_messages);
    exception when sqlstate 'PT409' then
        get stacked diagnostics v_detail = pg_exception_detail;
        if v_detail not in ('DIARY_DATE_CONFLICT','IDEMPOTENCY_CONFLICT') or v_detail is null then raise; end if;
        -- 명확한 확정 충돌은 DB 장애/AI 결과 불명과 구분하고 잠금을 해제합니다.
        update public.chat_requests set state = 'failed',updated_at = pg_catalog.clock_timestamp(),
            error = pg_catalog.jsonb_build_object('status',409,'code',v_detail,'message',
                case v_detail when 'DIARY_DATE_CONFLICT' then '날짜 중복' else '요청 ID 충돌' end,
                'retryable',false) where id = r.id;
        update public.chat_sessions set active_request_id = null,version = version + 1,
            updated_at = pg_catalog.clock_timestamp() where session_id = s.session_id;
        return pg_catalog.jsonb_build_object('error',v_detail);
    end;
    update public.chat_requests set state = 'succeeded',result = pg_catalog.jsonb_build_object('diary_id',v_diary_id),
        error = null,updated_at = pg_catalog.clock_timestamp() where id = r.id returning * into r;
    update public.chat_sessions set lifecycle = 'confirmed',confirmed_diary_id = v_diary_id,
        active_request_id = null,version = version + 1,updated_at = pg_catalog.clock_timestamp()
        where session_id = s.session_id returning * into s;
    return pg_catalog.jsonb_build_object('session',pg_catalog.to_jsonb(s),'request',pg_catalog.to_jsonb(r),'acquired',false);
end;
$function$;

revoke all on function public.reserve_chat_request(uuid,text,text,text,text,jsonb,uuid) from public,anon,authenticated;
revoke all on function public.transition_chat_request(uuid,text,uuid,uuid,bigint,text,jsonb,jsonb,integer) from public,anon,authenticated;
revoke all on function public.confirm_chat_session_diary(uuid,text,uuid,uuid,bigint) from public,anon,authenticated;
grant execute on function public.reserve_chat_request(uuid,text,text,text,text,jsonb,uuid) to service_role;
grant execute on function public.transition_chat_request(uuid,text,uuid,uuid,bigint,text,jsonb,jsonb,integer) to service_role;
grant execute on function public.confirm_chat_session_diary(uuid,text,uuid,uuid,bigint) to service_role;
