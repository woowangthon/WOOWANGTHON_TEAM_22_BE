-- 2026-11-11 / REVIEW BEFORE RUN / 실제 DB 미접속·미실행.
-- 기존 integration_preflight / integration_verify / v04_verify / v04_preflight 재사용.
-- docs/DB_INTEGRATION_PREP_20261110.txt 0절과 SQL 내용을 먼저 사용자·ChatGPT가 검토한다.
-- 전체 파일 실행 금지. 승인된 Q 한 개만 복사. 각 Q는 독립 SELECT 1개다.
-- Q03은 Q02 통과 뒤에만 검토한다. 오류/예상외 객체 발견 시 다음 Q도 중단한다.
-- 앱 데이터/COUNT/함수 본문/정책 조건/기본값/인덱스 표현식 원문은 출력하지 않는다.
-- 사용자 정의 함수/RPC 호출, 명시적 잠금/트랜잭션 제어, 변경문 없음.

-- Q01 BEGIN: 접속 환경 참고. DB명·역할은 프로젝트 식별 증거가 아니다.
select pg_catalog.current_database() as database_name, current_user as editor_role,
       pg_catalog.current_setting('server_version') as server_version;
-- Q01 END

-- Q02 BEGIN: 이력 테이블 구조 확인. 1행, relation_kind=r, rls_enabled=false,
-- version_type=text 또는 character varying, version_is_builtin=true,
-- version_is_generated=false일 때만 Q03 검토 가능.
select n.nspname, c.relname, c.relkind as relation_kind, c.relrowsecurity as rls_enabled,
       pg_catalog.format_type(a.atttypid, a.atttypmod) as version_type,
       (tn.nspname = 'pg_catalog' and t.typname in ('text', 'varchar')) as version_is_builtin,
       (a.attgenerated <> '') as version_is_generated
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
left join pg_catalog.pg_attribute a on a.attrelid = c.oid and a.attname = 'version'
    and a.attnum > 0 and not a.attisdropped
left join pg_catalog.pg_type t on t.oid = a.atttypid
left join pg_catalog.pg_namespace tn on tn.oid = t.typnamespace
where n.nspname = 'supabase_migrations' and c.relname = 'schema_migrations';
-- Q02 END

-- Q03 BEGIN: 조건부 이력 조회. Q02 통과 뒤에만. version만 읽으며 statements/name 등은 제외.
-- 001~004 recorded=true, 제외005=false 예상. 누락은 수동 적용 가능성 때문에 미적용 증거 아님.
with expected(version, stage) as (values
    ('20261107000100', '001'), ('20261107000200', '002'),
    ('20261107000300', '003'), ('20261108000400', '004'),
    ('20261109000500', 'excluded-005'))
select e.stage, e.version,
       exists (select 1 from supabase_migrations.schema_migrations m
               where m.version = e.version) as recorded
from expected e order by e.version;
-- Q03 END

-- Q04 BEGIN: 관련 객체 존재/RLS/소유자/직접 ACL. absent도 출력.
-- 001-004/dependency 존재, room/comic/v0.4/excluded-005 부재 예상.
-- relkind r=table, p=partitioned table, v=view, i=index. 예상외 종류는 중단.
with expected(stage, schema_name, object_name) as (values
('001-004', 'public', 'diaries'),
    ('001-004', 'public', 'diary_messages'),
    ('001-004', 'public', 'diary_assets'),
    ('room', 'public', 'member_reward_legacy_diaries'),
    ('room', 'public', 'member_reward_wallets'),
    ('room', 'public', 'member_daily_reward_grants'),
    ('room', 'public', 'shop_items'),
    ('room', 'public', 'member_owned_items'),
    ('room', 'public', 'member_room_placements'),
    ('room', 'public', 'member_room_settings'),
    ('room', 'public', 'member_tree_states'),
    ('room', 'public', 'member_tree_watering_events'),
    ('comic', 'public', 'comic_previews'),
    ('comic', 'public', 'diary_comics'),
    ('comic', 'public', 'comic_attach_requests'),
    ('v0.4', 'public', 'diary_edit_requests'),
    ('v0.4', 'public', 'diary_delete_requests'),
    ('v0.4', 'public', 'shop_purchase_receipts'),
    ('v0.4', 'public', 'shop_refund_receipts'),
    ('dependency', 'auth', 'users'),
    ('dependency', 'storage', 'objects'),
    ('dependency', 'storage', 'buckets'),
    ('excluded-005', 'public', 'chat_sessions'),
    ('excluded-005', 'public', 'chat_requests'))
select e.stage, e.schema_name, e.object_name, (c.oid is not null) as object_exists,
       c.relkind, c.relrowsecurity, c.relforcerowsecurity,
       pg_catalog.pg_get_userbyid(c.relowner) as owner_role, c.relacl
from expected e
left join pg_catalog.pg_namespace n on n.nspname = e.schema_name
left join pg_catalog.pg_class c on c.relnamespace = n.oid and c.relname = e.object_name
order by e.stage, e.schema_name, e.object_name;
-- Q04 END

-- Q05 BEGIN: 열 이름/타입/null/default 유무만 확인. 내용과 기본값 식은 읽지 않는다.
-- auth.users 의존4열과 storage.objects 정책 의존 열도 메타데이터만 조회.
select n.nspname as schema_name, c.relname as table_name, a.attnum as ordinal_position,
       a.attname as column_name, pg_catalog.format_type(a.atttypid, a.atttypmod) as data_type,
       a.attnotnull as not_null, a.atthasdef as has_default,
       a.attidentity, a.attgenerated
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
where (n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts'))
   or (n.nspname = 'auth' and c.relname = 'users'
       and a.attname in ('id', 'is_anonymous', 'role', 'deleted_at'))
   or (n.nspname = 'storage' and c.relname = 'objects' and a.attname in ('bucket_id', 'name'))
order by n.nspname, c.relname, a.attnum;
-- Q05 END

-- Q06 BEGIN: PK/UQ/FK 정의만 출력. CHECK는 식 없이 이름·검증 여부만 확인.
select n.nspname as schema_name, c.relname as table_name, k.conname, k.contype,
       k.convalidated, k.condeferrable, k.condeferred,
       case when k.contype in ('p', 'u', 'f')
            then pg_catalog.pg_get_constraintdef(k.oid, false) end as key_definition
from pg_catalog.pg_constraint k
join pg_catalog.pg_class c on c.oid = k.conrelid
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where (n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts'))
   or (n.nspname = 'auth' and c.relname = 'users' and k.contype = 'p')
order by n.nspname, c.relname, k.conname;
-- Q06 END

-- Q07 BEGIN: 인덱스·부분 여부·열 순서·정상 여부. 표현식/조건 원문은 출력하지 않는다.
-- diaries_user_date_uq: 최초에는 unique=true/partial=false 및 Q06 UNIQUE 제약.
-- v0.4 뒤에만 partial=true/predicate_is_live_diary=true 예상.
-- predicate boolean은 로컬 조건과 문자열 비교이며 의미 동등성 증명이 아니다.
select n.nspname as schema_name, c.relname as table_name, ix.relname as index_name,
       i.indisprimary, i.indisunique, i.indisvalid, i.indisready,
       (i.indpred is not null) as is_partial, (i.indexprs is not null) as has_expressions,
       i.indnkeyatts as key_column_count, i.indnatts as total_column_count,
       array(select a.attname from pg_catalog.unnest(i.indkey) with ordinality as keys(attnum, pos)
             left join pg_catalog.pg_attribute a on a.attrelid = c.oid and a.attnum = keys.attnum
             order by keys.pos) as ordered_columns,
       case when n.nspname = 'public' and c.relname = 'diaries'
                 and ix.relname = 'diaries_user_date_uq' and i.indpred is not null
            then pg_catalog.pg_get_expr(i.indpred, i.indrelid, false) = '(deleted_at IS NULL)'
            else null end as predicate_is_live_diary
from pg_catalog.pg_index i
join pg_catalog.pg_class c on c.oid = i.indrelid
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_class ix on ix.oid = i.indexrelid
where (n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts'))
   or (n.nspname = 'public' and ix.relname in ('diaries_user_date_uq',
       'diaries_user_confirmation_request_uq', 'reward_member_date_v04_uq'))
order by n.nspname, c.relname, ix.relname;
-- Q07 END

-- Q08 BEGIN: 정책 이름/역할/명령/허용·제한 모드만. 조건식과 함수는 실행하지 않는다.
-- storage.objects 전체 정책의 존재를 확인하며 실제 객체 경로는 읽지 않는다.
select n.nspname as schema_name, c.relname as table_name, p.polname,
       p.polpermissive, p.polcmd,
       array(select case when x.role_oid = 0 then 'PUBLIC' else r.rolname::text end
             from pg_catalog.unnest(p.polroles) as x(role_oid)
             left join pg_catalog.pg_roles r on r.oid = x.role_oid order by x.role_oid) as target_roles,
       (p.polqual is not null) as has_using, (p.polwithcheck is not null) as has_with_check
from pg_catalog.pg_policy p
join pg_catalog.pg_class c on c.oid = p.polrelid
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where (n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts')) or (n.nspname = 'storage' and c.relname = 'objects')
order by n.nspname, c.relname, p.polname;
-- Q08 END

-- Q09 BEGIN: RPC·의존 함수의 동명 오버로드/서명/반환형/권한. 함수 본문과 인수 기본값 제외.
-- auth/storage 함수도 호출하지 않는다. 005 함수명은 예상외 설치 감지 전용.
select n.nspname as schema_name, p.proname, p.prokind,
       pg_catalog.pg_get_function_identity_arguments(p.oid) as identity_arguments,
       pg_catalog.pg_get_function_result(p.oid) as result_type,
       l.lanname, p.prosecdef, p.provolatile,
       array(select cfg from pg_catalog.unnest(p.proconfig) as settings(cfg)
             where cfg like 'search_path=%') as search_path_settings,
       pg_catalog.pg_get_userbyid(p.proowner) as owner_role, p.proacl,
       r.rolname as checked_role,
       pg_catalog.has_function_privilege(r.oid, p.oid, 'EXECUTE') as can_execute
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
join pg_catalog.pg_language l on l.oid = p.prolang
cross join pg_catalog.pg_roles r
where r.rolname in ('anon', 'authenticated', 'service_role') and ((n.nspname = 'public' and p.proname in (
    'confirm_member_diary', 'reserve_diary_drawing', 'complete_diary_drawing',
    'room_guard_owned_catalog', 'room_guard_item_usage', 'room_snapshot', 'member_room_command',
    'comic_panels_valid', 'save_comic_preview_result', 'complete_comic_preview_asset',
    'attach_diary_comic', 'member_diary_edit',
    'reserve_chat_request', 'transition_chat_request', 'confirm_chat_session_diary'))
    or (n.nspname = 'auth' and p.proname in ('uid', 'jwt'))
    or (n.nspname = 'storage' and p.proname = 'foldername'))
order by n.nspname, p.proname, identity_arguments, r.rolname;
-- Q09 END

-- Q10 BEGIN: 관련 역할 속성. 암호/회원정보는 조회하지 않는다.
select rolname, rolsuper, rolinherit, rolbypassrls
from pg_catalog.pg_roles where rolname in ('anon', 'authenticated', 'service_role')
order by rolname;
-- Q10 END

-- Q11 BEGIN: 스키마 직접 ACL과 유효 권한. NULL ACL을 권한 없음으로 해석하지 않는다.
select n.nspname, n.nspacl, r.rolname,
       pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE') as can_use,
       pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE') as can_create
from pg_catalog.pg_namespace n cross join pg_catalog.pg_roles r
where n.nspname in ('public', 'auth', 'storage', 'supabase_migrations')
  and r.rolname in ('anon', 'authenticated', 'service_role')
order by n.nspname, r.rolname;
-- Q11 END

-- Q12 BEGIN: 테이블·열 유효 권한. 권한 boolean은 RLS 접근 허용 증거가 아니다.
select n.nspname, c.relname, r.rolname,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'SELECT') as table_select,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'INSERT') as table_insert,
       pg_catalog.has_any_column_privilege(r.oid, c.oid, 'INSERT') as any_column_insert,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'UPDATE') as table_update,
       pg_catalog.has_any_column_privilege(r.oid, c.oid, 'UPDATE') as any_column_update,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'DELETE') as table_delete,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'TRUNCATE') as table_truncate,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'REFERENCES') as table_references,
       pg_catalog.has_table_privilege(r.oid, c.oid, 'TRIGGER') as table_trigger
from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace
cross join pg_catalog.pg_roles r
where c.relkind in ('r', 'p') and r.rolname in ('anon', 'authenticated', 'service_role')
  and ((n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts')) or (n.nspname = 'auth' and c.relname = 'users')
       or (n.nspname = 'storage' and c.relname in ('objects', 'buckets')))
order by n.nspname, c.relname, r.rolname;
-- Q12 END

-- Q13 BEGIN: 열 ACL·유효 권한. auth.users는 의존4열만 메타데이터 확인.
-- Q12 테이블 권한과 함께 읽는다. 함수 내부 행 잠금은 수행하지 않는다.
select n.nspname, c.relname, a.attname, a.attacl, r.rolname,
       pg_catalog.has_column_privilege(r.oid, c.oid, a.attnum, 'SELECT') as can_select,
       pg_catalog.has_column_privilege(r.oid, c.oid, a.attnum, 'INSERT') as can_insert,
       pg_catalog.has_column_privilege(r.oid, c.oid, a.attnum, 'UPDATE') as can_update
from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
cross join pg_catalog.pg_roles r
where c.relkind in ('r', 'p') and r.rolname in ('anon', 'authenticated', 'service_role')
  and ((n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts')) or (n.nspname = 'auth' and c.relname = 'users'
       and a.attname in ('id', 'is_anonymous', 'role', 'deleted_at')))
  and (a.attacl is not null or (n.nspname = 'auth' and c.relname = 'users')
       or (n.nspname = 'public' and c.relname in ('diaries', 'diary_assets', 'shop_items',
           'comic_previews', 'diary_edit_requests')))
order by n.nspname, c.relname, a.attnum, r.rolname;
-- Q13 END

-- Q14 BEGIN: 사용자 트리거 존재/함수명. 트리거 정의·인수·본문은 출력·실행하지 않는다.
select n.nspname, c.relname, t.tgname, t.tgenabled,
       fn.nspname as function_schema, p.proname as trigger_function
from pg_catalog.pg_trigger t
join pg_catalog.pg_class c on c.oid = t.tgrelid
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_proc p on p.oid = t.tgfoid
join pg_catalog.pg_namespace fn on fn.oid = p.pronamespace
where not t.tgisinternal and (n.nspname = 'public' and c.relname in (
    'diaries', 'diary_messages', 'diary_assets',
    'member_reward_legacy_diaries', 'member_reward_wallets', 'member_daily_reward_grants',
    'shop_items', 'member_owned_items', 'member_room_placements',
    'member_room_settings', 'member_tree_states', 'member_tree_watering_events',
    'comic_previews', 'diary_comics', 'comic_attach_requests',
    'diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts',
    'shop_refund_receipts'))
order by n.nspname, c.relname, t.tgname;
-- Q14 END

-- Q15 BEGIN: 테이블 생성과 충돌할 동명 타입. 타입 정의/값은 출력하지 않는다.
-- 기존3테이블 row type은 정상. 후보/005 이름이 있으면 Q04와 대조 후 중단.
with expected(stage, schema_name, object_name) as (values
('001-004', 'public', 'diaries'),
    ('001-004', 'public', 'diary_messages'),
    ('001-004', 'public', 'diary_assets'),
    ('room', 'public', 'member_reward_legacy_diaries'),
    ('room', 'public', 'member_reward_wallets'),
    ('room', 'public', 'member_daily_reward_grants'),
    ('room', 'public', 'shop_items'),
    ('room', 'public', 'member_owned_items'),
    ('room', 'public', 'member_room_placements'),
    ('room', 'public', 'member_room_settings'),
    ('room', 'public', 'member_tree_states'),
    ('room', 'public', 'member_tree_watering_events'),
    ('comic', 'public', 'comic_previews'),
    ('comic', 'public', 'diary_comics'),
    ('comic', 'public', 'comic_attach_requests'),
    ('v0.4', 'public', 'diary_edit_requests'),
    ('v0.4', 'public', 'diary_delete_requests'),
    ('v0.4', 'public', 'shop_purchase_receipts'),
    ('v0.4', 'public', 'shop_refund_receipts'),
    ('dependency', 'auth', 'users'),
    ('dependency', 'storage', 'objects'),
    ('dependency', 'storage', 'buckets'),
    ('excluded-005', 'public', 'chat_sessions'),
    ('excluded-005', 'public', 'chat_requests'))
select e.stage, e.object_name, t.typtype, c.relname as associated_relation
from expected e join pg_catalog.pg_namespace n on n.nspname = e.schema_name
join pg_catalog.pg_type t on t.typnamespace = n.oid and t.typname = e.object_name
left join pg_catalog.pg_class c on c.oid = t.typrelid
where e.schema_name = 'public'
order by e.stage, e.object_name;
-- Q15 END
