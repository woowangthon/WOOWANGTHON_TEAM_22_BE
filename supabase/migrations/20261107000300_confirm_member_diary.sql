-- 로컬 준비본: 원격 미적용, 001·002 적용 상태와 실제 스키마 선확인
alter table public.diaries add column confirmation_fingerprint text;
alter table public.diaries add constraint diaries_confirmation_fingerprint_ck
    check (confirmation_fingerprint ~ '^[0-9a-f]{64}$');

-- 기존 행 보존, 신규 확정 요청의 회원별 고유성
create unique index diaries_user_confirmation_request_uq
    on public.diaries (user_id, source_client_request_id)
    where confirmation_fingerprint is not null;

-- SECURITY INVOKER 실행에 필요한 서버 역할 권한
grant usage on schema public, auth to service_role;
grant select (id, is_anonymous, role, deleted_at) on auth.users to service_role;
grant select, insert on public.diaries, public.diary_messages to service_role;

create function public.confirm_member_diary(
    p_user_id uuid,
    p_session_id text,
    p_client_request_id text,
    p_history_revision integer,
    p_fingerprint text,
    p_weather text,
    p_confirmed_at timestamptz,
    p_ai_draft jsonb,
    p_messages jsonb
) returns uuid
language plpgsql
security invoker
set search_path = ''
as $function$
declare
    v_existing public.diaries%rowtype;
    v_diary_id uuid;
    v_date date;
    v_constraint text;
begin
    -- 회원 유효성 확인
    if p_user_id is null or not exists (
        select 1 from auth.users as u
        where u.id = p_user_id and u.is_anonymous is false
            and u.role = 'authenticated' and u.deleted_at is null
    ) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    if p_fingerprint is null or p_fingerprint !~ '^[0-9a-f]{64}$'
        or p_confirmed_at is null or not pg_catalog.isfinite(p_confirmed_at)
        or p_session_id is null or p_session_id !~ '^ses_[0-9a-f]{32}$'
        or p_client_request_id is null
        or p_client_request_id !~ '^[A-Za-z0-9_-]{1,128}$'
        or p_history_revision is null or p_history_revision < 1
        or (p_weather is not null and p_weather not in ('sunny','cloudy','snowy','rainy'))
    then
        raise exception using errcode = '22023', message = 'invalid confirmation';
    end if;

    -- 동일 회원 저장 직렬화와 재시작 이후 중복 요청 확인
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_user_id::text, 0));
    select d.* into v_existing from public.diaries as d
    where d.user_id = p_user_id and d.source_client_request_id = p_client_request_id
        and d.confirmation_fingerprint is not null;
    if found then
        if v_existing.confirmation_fingerprint <> p_fingerprint then
            raise exception using errcode = 'PT409', message = 'request conflict';
        end if;
        return v_existing.id;
    end if;

    -- BE 확정 시각의 서울 날짜
    v_date := (p_confirmed_at at time zone 'Asia/Seoul')::date;
    if exists (select 1 from public.diaries as d
               where d.user_id = p_user_id and d.diary_date = v_date) then
        raise exception using errcode = 'PT410', message = 'date conflict';
    end if;

    -- 원본 대화 구조·순서 확인
    if p_messages is null or pg_catalog.jsonb_typeof(p_messages) <> 'array' then
        raise exception using errcode = '22023', message = 'invalid messages';
    end if;
    if pg_catalog.jsonb_array_length(p_messages) not between 3 and 60 then
        raise exception using errcode = '22023', message = 'invalid messages';
    end if;
    if exists (
        select 1 from pg_catalog.jsonb_array_elements(p_messages)
            with ordinality as m(value, ordinal)
        where not coalesce(
            pg_catalog.jsonb_typeof(m.value) = 'object'
            and pg_catalog.jsonb_typeof(m.value -> 'position') = 'number'
            and m.value ->> 'position' = (m.ordinal - 1)::text
            and pg_catalog.jsonb_typeof(m.value -> 'role') = 'string'
            and m.value ->> 'role' in ('child', 'assistant')
            and pg_catalog.jsonb_typeof(m.value -> 'text') = 'string', false
        )
    ) or not exists (
        select 1 from pg_catalog.jsonb_array_elements(p_messages) as m(value)
        where m.value ->> 'role' = 'child'
    ) then
        raise exception using errcode = '22023', message = 'invalid messages';
    end if;

    insert into public.diaries (
        user_id, diary_date, weather, confirmed_text, confirmed_at,
        source_session_id, source_history_revision, source_client_request_id,
        ai_draft, confirmation_fingerprint
    ) values (
        p_user_id, v_date, p_weather, p_ai_draft ->> 'text', p_confirmed_at,
        p_session_id, p_history_revision, p_client_request_id, p_ai_draft, p_fingerprint
    ) returning id into v_diary_id;

    insert into public.diary_messages (diary_id, user_id, position, role, text)
    select v_diary_id, p_user_id, (m.value ->> 'position')::integer,
        m.value ->> 'role', m.value ->> 'text'
    from pg_catalog.jsonb_array_elements(p_messages) with ordinality as m(value, ordinal)
    order by m.ordinal;
    return v_diary_id;
exception when unique_violation then
    -- 예외 블록 전체 쓰기 롤백
    get stacked diagnostics v_constraint = constraint_name;
    if v_constraint = 'diaries_user_date_uq' then
        raise exception using errcode = 'PT410', message = 'date conflict';
    end if;
    raise;
end;
$function$;

revoke all on function public.confirm_member_diary(
    uuid, text, text, integer, text, text, timestamptz, jsonb, jsonb
) from public, anon, authenticated;
grant execute on function public.confirm_member_diary(
    uuid, text, text, integer, text, text, timestamptz, jsonb, jsonb
) to service_role;
