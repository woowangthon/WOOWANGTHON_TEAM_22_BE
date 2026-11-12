-- 미실행 수동 검증: 폐기 가능한 로컬 Supabase 전용, 001·003 선적용
-- 원격 프로젝트 실행 금지, 실제 회원 사용 금지, 마지막 ROLLBACK 필수
-- 전체 파일 단일 실행, 부분 선택 실행 금지
begin;
set local lock_timeout = '3s';
set local statement_timeout = '30s';
set local idle_in_transaction_session_timeout = '60s';
do $local_guard$
declare
    -- 폐기 가능한 로컬 DB 확인 후 사용자 직접 변경
    v_disposable_local boolean := false;
begin
if not v_disposable_local then raise exception '로컬 격리 환경 미확인'; end if;
if exists (select 1 from auth.users where id = '33333333-3333-4333-8333-333333333333') then
    raise exception '합성 회원 ID 사용 중';
end if;
-- 성공 시에도 합성 회원과 모든 쓰기 취소
begin
insert into auth.users (id, aud, role, is_anonymous)
values ('33333333-3333-4333-8333-333333333333', 'authenticated', 'authenticated', false);

declare
    v_signature text := 'public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)';
    v_operator name := current_user;
    v_role text;
    v_rejected boolean;
begin
    if pg_catalog.has_function_privilege('anon', v_signature, 'execute')
        or pg_catalog.has_function_privilege('authenticated', v_signature, 'execute')
        or not pg_catalog.has_function_privilege('service_role', v_signature, 'execute')
    then
        raise exception 'RPC 실행 권한 불일치';
    end if;
    if pg_catalog.has_table_privilege('authenticated', 'public.diaries', 'insert')
        or pg_catalog.has_table_privilege('authenticated', 'public.diary_messages', 'insert')
        or pg_catalog.has_table_privilege('anon', 'public.diaries', 'insert')
    then
        raise exception '일반 회원 쓰기 권한 존재';
    end if;
    foreach v_role in array array['anon','authenticated'] loop
        execute format('set local role %I', v_role);
        v_rejected := false;
        begin
            perform public.confirm_member_diary(null, null, null, null, null, null, null, null, null);
        exception when insufficient_privilege then
            v_rejected := true;
        end;
        execute format('set local role %I', v_operator);
        if not v_rejected then raise exception '일반 회원 RPC 호출 허용'; end if;
    end loop;
end;

execute 'set local role service_role';
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
    v_constraint text;
begin
    v_id := public.confirm_member_diary(v_member, v_session, 'local-proof-1', 1,
        repeat('a', 64), null, '2035-01-01 15:00:00+00', v_draft, v_messages);
    v_repeated := public.confirm_member_diary(v_member, v_session, 'local-proof-1', 1,
        repeat('a', 64), null, '2035-01-02 15:00:00+00', v_draft, v_messages);
    if v_id <> v_repeated
        or (select diary_date from public.diaries where id = v_id) <> date '2035-01-02'
        or (select count(*) from public.diary_messages where diary_id = v_id) <> 3
        or (select jsonb_agg(jsonb_build_object('position',position,'role',role,'text',text)
            order by position) from public.diary_messages where diary_id = v_id)
            is distinct from v_messages
    then
        raise exception '저장 또는 멱등성 불일치';
    end if;

    v_rejected := false;
    begin
        perform public.confirm_member_diary(v_member, v_session, 'local-proof-1', 1,
            repeat('b', 64), null, '2035-01-01 15:00:00+00', v_draft, v_messages);
    exception when sqlstate 'PT409' then
        get stacked diagnostics v_detail = pg_exception_detail, v_message = message_text;
        v_rejected := v_detail = 'IDEMPOTENCY_CONFLICT' and v_message = '요청 내용 불일치';
    end;
    if not v_rejected then raise exception '요청 충돌 불일치'; end if;

    v_rejected := false;
    begin
        perform public.confirm_member_diary(v_member, v_session, 'local-proof-2', 1,
            repeat('c', 64), null, '2035-01-01 15:00:00+00', v_draft, v_messages);
    exception when sqlstate 'PT409' then
        get stacked diagnostics v_detail = pg_exception_detail, v_message = message_text;
        v_rejected := v_detail = 'DIARY_DATE_CONFLICT' and v_message = '날짜 중복';
    end;
    if not v_rejected then raise exception '날짜 충돌 불일치'; end if;

    -- 부모 INSERT 이후 메시지 CHECK 실패에 따른 전체 롤백
    v_rejected := false;
    begin
        perform public.confirm_member_diary(v_member, v_session, 'local-proof-failure', 1,
            repeat('d', 64), null, '2035-01-02 15:00:00+00', v_draft,
            jsonb_set(v_messages, '{2,text}', '""'::jsonb));
    exception when check_violation then
        get stacked diagnostics v_constraint = constraint_name;
        v_rejected := v_constraint = 'diary_messages_text_ck';
    end;
    if not v_rejected or exists (
        select 1 from public.diaries where user_id = v_member
            and source_client_request_id = 'local-proof-failure'
    ) or (select count(*) from public.diary_messages where user_id = v_member) <> 3 then
        raise exception '부분 저장 잔여 데이터';
    end if;
end;
raise exception using errcode = 'ZV001', message = '검증 쓰기 취소';
exception when sqlstate 'ZV001' then
    null;
end;
if exists (select 1 from auth.users where id = '33333333-3333-4333-8333-333333333333')
    or exists (select 1 from public.diaries where user_id = '33333333-3333-4333-8333-333333333333')
    or exists (select 1 from public.diary_messages where user_id = '33333333-3333-4333-8333-333333333333')
then raise exception '로컬 데이터 불변 검사 실패'; end if;
perform pg_catalog.set_config('rpc_verification.result', 'LOCAL_RPC_SQL_CHECKS_PASSED', true);
end;
$local_guard$;
select current_setting('rpc_verification.result', true) as verification_result;
rollback;
