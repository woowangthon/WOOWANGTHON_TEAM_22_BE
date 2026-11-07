-- 미실행 수동 검증: 폐기 가능한 로컬 Supabase 전용, 001·003 선적용
-- 원격 프로젝트 실행 금지, 실제 회원 사용 금지, 마지막 ROLLBACK 필수
begin;
insert into auth.users (id, aud, role, is_anonymous)
values ('33333333-3333-4333-8333-333333333333', 'authenticated', 'authenticated', false);

do $permissions$
declare
    v_signature text := 'public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)';
begin
    if pg_catalog.has_function_privilege('anon', v_signature, 'execute')
        or pg_catalog.has_function_privilege('authenticated', v_signature, 'execute')
        or not pg_catalog.has_function_privilege('service_role', v_signature, 'execute')
    then
        raise exception 'unexpected RPC privileges';
    end if;
    if pg_catalog.has_table_privilege('authenticated', 'public.diaries', 'insert')
        or pg_catalog.has_table_privilege('authenticated', 'public.diary_messages', 'insert')
        or pg_catalog.has_table_privilege('anon', 'public.diaries', 'insert')
    then
        raise exception 'unexpected member write privileges';
    end if;
end;
$permissions$;

set local role service_role;
do $verify$
declare
    v_member uuid := '33333333-3333-4333-8333-333333333333';
    v_session text := 'ses_33333333333333333333333333333333';
    v_draft jsonb := '{"draft_id":"draft_33333333333333333333333333333333","status":"draft","text":"synthetic diary","pages":[],"sources":[],"fallback":true,"requires_confirmation":true,"mock":true}';
    v_messages jsonb := '[{"position":0,"role":"assistant","text":"synthetic question"},{"position":1,"role":"child","text":"synthetic answer"},{"position":2,"role":"assistant","text":"synthetic reply"}]';
    v_id uuid;
    v_repeated uuid;
    v_rejected boolean;
    v_detail text;
    v_message text;
begin
    v_id := public.confirm_member_diary(v_member, v_session, 'local-proof-1', 1,
        repeat('a', 64), null, '2035-01-01 15:00:00+00', v_draft, v_messages);
    v_repeated := public.confirm_member_diary(v_member, v_session, 'local-proof-1', 1,
        repeat('a', 64), null, '2035-01-02 15:00:00+00', v_draft, v_messages);
    if v_id <> v_repeated
        or (select diary_date from public.diaries where id = v_id) <> date '2035-01-02'
        or (select count(*) from public.diary_messages where diary_id = v_id) <> 3
    then
        raise exception 'save/replay/date failure';
    end if;

    v_rejected := false;
    begin
        perform public.confirm_member_diary(v_member, v_session, 'local-proof-1', 1,
            repeat('b', 64), null, '2035-01-01 15:00:00+00', v_draft, v_messages);
    exception when sqlstate 'PT409' then
        get stacked diagnostics v_detail = pg_exception_detail, v_message = message_text;
        v_rejected := v_detail = 'IDEMPOTENCY_CONFLICT' and v_message = '요청 내용 불일치';
    end;
    if not v_rejected then raise exception 'request conflict missing'; end if;

    v_rejected := false;
    begin
        perform public.confirm_member_diary(v_member, v_session, 'local-proof-2', 1,
            repeat('c', 64), null, '2035-01-01 15:00:00+00', v_draft, v_messages);
    exception when sqlstate 'PT409' then
        get stacked diagnostics v_detail = pg_exception_detail, v_message = message_text;
        v_rejected := v_detail = 'DIARY_DATE_CONFLICT' and v_message = '날짜 중복';
    end;
    if not v_rejected then raise exception 'date conflict missing'; end if;

    -- 부모 INSERT 이후 메시지 CHECK 실패에 따른 전체 롤백
    v_rejected := false;
    begin
        perform public.confirm_member_diary(v_member, v_session, 'local-proof-failure', 1,
            repeat('d', 64), null, '2035-01-02 15:00:00+00', v_draft,
            jsonb_set(v_messages, '{2,text}', '""'::jsonb));
    exception when check_violation then
        v_rejected := true;
    end;
    if not v_rejected or exists (
        select 1 from public.diaries where user_id = v_member
            and source_client_request_id = 'local-proof-failure'
    ) or (select count(*) from public.diary_messages where user_id = v_member) <> 3 then
        raise exception 'atomic rollback failure';
    end if;
end;
$verify$;
reset role;
rollback;
