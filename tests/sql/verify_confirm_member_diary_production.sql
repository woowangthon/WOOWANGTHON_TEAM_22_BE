-- 사용자 사전 검토 필수, 원격 미실행 검증본
-- SQL Editor 전체 파일 단일 실행, 부분 선택 실행 금지
begin;
set local lock_timeout = '3s';
set local statement_timeout = '30s';
set local idle_in_transaction_session_timeout = '60s';
set local client_min_messages = notice;

do $verify$
declare
    -- 검토 완료 후 사용자 직접 변경
    v_reviewed boolean := false;
    v_signature text := 'public.confirm_member_diary(uuid,text,text,integer,text,text,timestamptz,jsonb,jsonb)';
    v_operator name := current_user;
    v_member_a uuid;
    v_member_b uuid;
    v_members uuid[];
    v_before jsonb;
    v_after jsonb;
    v_saved jsonb;
    v_id uuid;
    v_id_b uuid;
    v_repeated uuid;
    v_role text;
    v_detail text;
    v_message text;
    v_constraint text;
    v_rejected boolean;
    v_completed boolean := false;
    v_case record;
    v_session text := 'ses_33333333333333333333333333333333';
    v_time timestamptz := '2035-01-01 15:00:00+00';
    v_draft jsonb := '{"draft_id":"draft_33333333333333333333333333333333","status":"draft","text":"가상 인물의 테스트 일기","pages":[{"text":"가상 인물의 테스트 일기","source_ids":[1],"imageUrl":null}],"sources":[{"id":1,"text":"  가상 인물의 대답\n원문 보존  "}],"fallback":true,"requires_confirmation":true,"mock":true}';
    v_messages jsonb := '[{"position":0,"role":"assistant","text":"가상 질문"},{"position":1,"role":"child","text":"  가상 인물의 대답\n원문 보존  "},{"position":2,"role":"assistant","text":"가상 응답"}]';
begin
    if not v_reviewed then raise exception '사용자 검토 미완료'; end if;
    if v_operator <> 'postgres' then raise exception '관리자 역할 불일치'; end if;

    -- 로컬 003 본문 지문, 줄바꿈 정규화
    if not exists (
        select 1 from pg_catalog.pg_proc p
        where p.oid = pg_catalog.to_regprocedure(v_signature)
            and not p.prosecdef and p.prorettype = 'uuid'::regtype
            and p.proconfig = array['search_path=""']
            and p.proargnames = array['p_user_id','p_session_id','p_client_request_id',
                'p_history_revision','p_fingerprint','p_weather','p_confirmed_at',
                'p_ai_draft','p_messages']
            and pg_catalog.md5(pg_catalog.btrim(pg_catalog.replace(p.prosrc, chr(13), '')))
                = '771ead5fc1d43f8c7a0af79043d91c08'
    ) then raise exception 'RPC 정의 불일치'; end if;
    if exists (
        select 1 from pg_catalog.pg_trigger
        where tgrelid in ('public.diaries'::regclass, 'public.diary_messages'::regclass)
            and not tgisinternal and tgenabled <> 'D'
    ) or exists (
        select 1 from pg_catalog.pg_rewrite
        where ev_class in ('public.diaries'::regclass, 'public.diary_messages'::regclass)
            and rulename <> '_RETURN'
    ) then raise exception '추가 쓰기 부작용 검토 필요'; end if;

    -- 알려진 가상 fixture 소유자 한정
    select user_id into strict v_member_a from public.diaries
        where id = '77e0ebce-127d-4bb4-84ef-8d89d6c06a90' and diary_date = date '2026-11-07';
    select user_id into strict v_member_b from public.diaries
        where id = '49df086e-a986-41ef-8e95-e8997ff270cc' and diary_date = date '2026-11-07';
    if v_member_a = v_member_b then raise exception '테스트 회원 중복'; end if;
    v_members := array[v_member_a, v_member_b];
    if (select count(*) from auth.users where id = any(v_members)
        and is_anonymous is false and role = 'authenticated' and deleted_at is null) <> 2
    then raise exception '테스트 회원 상태 불일치'; end if;

    -- RPC와 동일한 회원별 잠금, 일관된 잠금 순서
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(least(v_member_a, v_member_b)::text, 0));
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(greatest(v_member_a, v_member_b)::text, 0));
    if exists (select 1 from public.diaries where user_id = any(v_members)
        and (diary_date in (date '2035-01-02', date '2035-01-03')
            or source_client_request_id like 'rpc-proof-%'))
    then raise exception '테스트 날짜 또는 요청 키 사용 중'; end if;

    if pg_catalog.has_function_privilege('anon', v_signature, 'execute')
        or pg_catalog.has_function_privilege('authenticated', v_signature, 'execute')
        or not pg_catalog.has_function_privilege('service_role', v_signature, 'execute')
        or exists (
            select 1 from pg_catalog.pg_proc p,
                lateral pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) a
            where p.oid = pg_catalog.to_regprocedure(v_signature) and a.grantee = 0
                and a.privilege_type = 'EXECUTE'
        ) then raise exception 'RPC 실행 권한 불일치'; end if;
    foreach v_role in array array['anon','authenticated'] loop
        if pg_catalog.has_table_privilege(v_role, 'public.diaries', 'insert')
            or pg_catalog.has_table_privilege(v_role, 'public.diary_messages', 'insert')
        then raise exception '일반 회원 쓰기 권한 존재'; end if;
    end loop;
    if exists (select 1 from pg_catalog.pg_class where oid in (
        'public.diaries'::regclass, 'public.diary_messages'::regclass,
        'public.diary_assets'::regclass) and (not relrowsecurity or relkind <> 'r'))
    then raise exception '테이블 또는 RLS 상태 불일치'; end if;

    -- A/B 전 행 스냅샷, 외부 출력 없음
    select jsonb_build_object(
        'users', (select coalesce(jsonb_agg(to_jsonb(u) order by u.id), '[]'::jsonb)
            from (select id, role, is_anonymous, deleted_at from auth.users
                where id = any(v_members)) u),
        'diaries', (select coalesce(jsonb_agg(to_jsonb(d) order by d.id), '[]'::jsonb)
            from public.diaries d where d.user_id = any(v_members)),
        'messages', (select coalesce(jsonb_agg(to_jsonb(m) order by m.id), '[]'::jsonb)
            from public.diary_messages m where m.user_id = any(v_members)),
        'assets', (select coalesce(jsonb_agg(to_jsonb(a) order by a.id), '[]'::jsonb)
            from public.diary_assets a where a.user_id = any(v_members))
    ) into v_before;

    -- 성공 시에도 취소하는 내부 트랜잭션 경계
    begin
        foreach v_role in array array['anon','authenticated'] loop
            execute format('set local role %I', v_role);
            v_rejected := false;
            begin
                perform public.confirm_member_diary(v_member_a, v_session, 'rpc-proof-denied', 1,
                    repeat('a',64), null, v_time, v_draft, v_messages);
            exception when insufficient_privilege then
                v_rejected := true;
            end;
            execute format('set local role %I', v_operator);
            if not v_rejected then raise exception '일반 회원 RPC 호출 허용'; end if;
        end loop;

        execute 'set local role service_role';
        if current_user <> 'service_role' then raise exception '서버 역할 전환 실패'; end if;
        v_id := public.confirm_member_diary(v_member_a, v_session, 'rpc-proof-save', 1,
            repeat('a',64), 'sunny', v_time, v_draft, v_messages);
        v_repeated := public.confirm_member_diary(v_member_a, v_session, 'rpc-proof-save', 1,
            repeat('a',64), 'sunny', v_time + interval '1 day', v_draft, v_messages);
        if v_id is null or v_id is distinct from v_repeated or not exists (
            select 1 from public.diaries where id = v_id and user_id = v_member_a
                and diary_date = date '2035-01-02' and weather = 'sunny'
                and confirmed_at = v_time and confirmed_text = v_draft ->> 'text'
                and ai_draft = v_draft and confirmation_fingerprint = repeat('a',64)
                and source_session_id = v_session and source_history_revision = 1
                and source_client_request_id = 'rpc-proof-save'
        ) then raise exception '저장 또는 멱등성 불일치'; end if;
        if (select jsonb_agg(jsonb_build_object('position',position,'role',role,'text',text)
            order by position) from public.diary_messages where diary_id = v_id
                and user_id = v_member_a) is distinct from v_messages
        then raise exception '원본 대화 불일치'; end if;

        -- 다른 회원 동일 키 독립성
        v_id_b := public.confirm_member_diary(v_member_b, v_session, 'rpc-proof-save', 1,
            repeat('b',64), null, v_time, v_draft, v_messages);
        if v_id_b is null or v_id_b = v_id or not exists (
            select 1 from public.diaries where id = v_id_b and user_id = v_member_b
                and diary_date = date '2035-01-02'
        ) then raise exception '회원별 요청 키 격리 실패'; end if;
        select jsonb_build_object(
            'diaries', (select jsonb_agg(to_jsonb(d) order by d.id)
                from public.diaries d where d.user_id = any(v_members)),
            'messages', (select jsonb_agg(to_jsonb(m) order by m.id)
                from public.diary_messages m where m.user_id = any(v_members))
        ) into v_saved;

        v_rejected := false;
        begin
            perform public.confirm_member_diary(v_member_a, v_session, 'rpc-proof-save', 1,
                repeat('c',64), 'sunny', v_time, v_draft, v_messages);
        exception when sqlstate 'PT409' then
            get stacked diagnostics v_detail = pg_exception_detail, v_message = message_text;
            v_rejected := v_detail = 'IDEMPOTENCY_CONFLICT' and v_message = '요청 내용 불일치';
        end;
        if not v_rejected then raise exception '요청 충돌 불일치'; end if;
        v_rejected := false;
        begin
            perform public.confirm_member_diary(v_member_a, v_session, 'rpc-proof-date', 1,
                repeat('c',64), null, v_time, v_draft, v_messages);
        exception when sqlstate 'PT409' then
            get stacked diagnostics v_detail = pg_exception_detail, v_message = message_text;
            v_rejected := v_detail = 'DIARY_DATE_CONFLICT' and v_message = '날짜 중복';
        end;
        if not v_rejected then raise exception '날짜 충돌 불일치'; end if;

        -- 비어 있는 다음 날짜의 데이터 검증
        for v_case in select * from (values
            ('member', '22023', null::text),
            ('fingerprint', '22023', null),
            ('session', '22023', null),
            ('request', '22023', null),
            ('revision', '22023', null),
            ('weather', '22023', null),
            ('time', '22023', null),
            ('messages_type', '22023', null),
            ('messages_count', '22023', null),
            ('messages_position', '22023', null),
            ('messages_role', '22023', null),
            ('messages_child', '22023', null),
            ('draft', '23514', 'diaries_ai_draft_ck'),
            ('message_text', '23514', 'diary_messages_text_ck')
        ) c(label, expected_state, expected_constraint) loop
            v_rejected := false;
            begin
                perform public.confirm_member_diary(
                    case when v_case.label = 'member' then null else v_member_a end,
                    case when v_case.label = 'session' then 'invalid' else v_session end,
                    case when v_case.label = 'request' then 'invalid!' else 'rpc-proof-invalid' end,
                    case when v_case.label = 'revision' then 0 else 1 end,
                    case when v_case.label = 'fingerprint' then 'invalid' else repeat('d',64) end,
                    case when v_case.label = 'weather' then 'invalid' else null end,
                    case when v_case.label = 'time' then 'infinity'::timestamptz
                        else v_time + interval '1 day' end,
                    case when v_case.label = 'draft' then
                        jsonb_set(v_draft, '{requires_confirmation}', 'false'::jsonb) else v_draft end,
                    case v_case.label
                        when 'messages_type' then '{}'::jsonb
                        when 'messages_count' then '[]'::jsonb
                        when 'messages_position' then jsonb_set(v_messages, '{2,position}', '9'::jsonb)
                        when 'messages_role' then jsonb_set(v_messages, '{2,role}', '"system"'::jsonb)
                        when 'messages_child' then jsonb_set(v_messages, '{1,role}', '"assistant"'::jsonb)
                        when 'message_text' then jsonb_set(v_messages, '{2,text}', '""'::jsonb)
                        else v_messages end);
            exception when others then
                get stacked diagnostics v_constraint = constraint_name;
                if sqlstate <> v_case.expected_state or (v_case.expected_constraint is not null
                    and v_constraint is distinct from v_case.expected_constraint) then
                    raise exception '데이터 검증 오류: %', v_case.label;
                end if;
                v_rejected := true;
            end;
            if not v_rejected then raise exception '잘못된 데이터 저장: %', v_case.label; end if;
        end loop;

        -- 실패한 부모·부분 메시지·충돌 잔여 행 부재
        select jsonb_build_object(
            'diaries', (select jsonb_agg(to_jsonb(d) order by d.id)
                from public.diaries d where d.user_id = any(v_members)),
            'messages', (select jsonb_agg(to_jsonb(m) order by m.id)
                from public.diary_messages m where m.user_id = any(v_members))
        ) into v_after;
        if v_after is distinct from v_saved then raise exception '부분 저장 잔여 데이터'; end if;
        v_completed := true;
        raise exception using errcode = 'ZV001', message = '검증 쓰기 취소';
    exception when sqlstate 'ZV001' then
        if not v_completed then raise exception '검증 완료 표식 없음'; end if;
    end;

    if current_user <> v_operator then raise exception '역할 복구 실패'; end if;
    select jsonb_build_object(
        'users', (select coalesce(jsonb_agg(to_jsonb(u) order by u.id), '[]'::jsonb)
            from (select id, role, is_anonymous, deleted_at from auth.users
                where id = any(v_members)) u),
        'diaries', (select coalesce(jsonb_agg(to_jsonb(d) order by d.id), '[]'::jsonb)
            from public.diaries d where d.user_id = any(v_members)),
        'messages', (select coalesce(jsonb_agg(to_jsonb(m) order by m.id), '[]'::jsonb)
            from public.diary_messages m where m.user_id = any(v_members)),
        'assets', (select coalesce(jsonb_agg(to_jsonb(a) order by a.id), '[]'::jsonb)
            from public.diary_assets a where a.user_id = any(v_members))
    ) into v_after;
    if v_before is distinct from v_after then raise exception '기존 데이터 불변 검사 실패'; end if;
    perform pg_catalog.set_config('rpc_verification.result', 'RPC_SQL_CHECKS_PASSED', true);
    raise notice 'RPC_SQL_CHECKS_PASSED: 내부 쓰기 취소 및 A/B 데이터 동일';
end;
$verify$;
select current_setting('rpc_verification.result', true) as verification_result;
rollback;
