-- 향후 승인 후 today-and-near Dashboard SQL Editor에서 조회만 실행한다. 현재 미실행.
select current_database(), current_user, version();
select version from supabase_migrations.schema_migrations order by version;
select id, public from storage.buckets where id = 'diary-private';

-- 001~004 실제 카탈로그와 아래 로컬 파일을 대조한다. 005는 별도 보류.
select table_schema, table_name, column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public' and table_name in ('diaries','diary_messages','diary_assets')
order by table_name, ordinal_position;
select conrelid::regclass, conname, pg_get_constraintdef(oid)
from pg_constraint where conrelid in ('public.diaries'::regclass, 'public.diary_assets'::regclass)
order by conrelid, conname;
select schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
from pg_policies where schemaname in ('public','storage') order by schemaname, tablename, policyname;
select rolname, rolbypassrls from pg_roles where rolname in ('anon','authenticated','service_role');

-- 후보 이름이 하나라도 이미 있으면 설치를 중단한다. 기존 후보 버전과 혼용 금지.
select name, to_regclass('public.' || name) as existing_object
from unnest(array['chat_sessions','chat_requests','member_reward_legacy_diaries',
    'member_reward_wallets','member_daily_reward_grants','shop_items','member_owned_items',
    'member_room_placements','member_room_settings','member_tree_states','member_tree_watering_events',
    'comic_previews','diary_comics','comic_attach_requests',
    'diary_edit_requests','diary_delete_requests','shop_purchase_receipts','shop_refund_receipts']) name;
-- v0.4 열/인덱스만 부분 생성된 상태도 중단한다. diaries_user_date_uq는 최초에는 UNIQUE constraint다.
select table_name, column_name from information_schema.columns
where table_schema='public' and (
    (table_name='diaries' and column_name in ('edit_version','updated_at','deleted_at','active_drawing_edit_id'))
    or (table_name='member_owned_items' and column_name='purchase_id')
    or (table_name='diary_comics' and column_name='source_edit_version'));
select tablename,indexname,indexdef from pg_indexes where schemaname='public'
and (tablename in ('diaries','member_daily_reward_grants') or indexname like '%v04%');
select n.nspname, p.proname, pg_get_function_identity_arguments(p.oid),
       p.prosecdef, p.proconfig, p.proacl
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in (
    'confirm_member_diary','reserve_diary_drawing','complete_diary_drawing',
    'member_room_command','room_snapshot','room_guard_owned_catalog','room_guard_item_usage',
    'comic_panels_valid','save_comic_preview_result','complete_comic_preview_asset','attach_diary_comic',
    'reserve_chat_request','transition_chat_request','confirm_chat_session_diary','member_diary_edit')
order by p.proname;
select n.nspname, p.proname, pg_get_functiondef(p.oid)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in (
    'confirm_member_diary','reserve_diary_drawing','complete_diary_drawing');

-- 개인정보/본문/경로 없이 기존 보호 대상 수량을 기록한다.
select 'diaries' as resource, count(*) as rows from public.diaries
union all select 'diary_messages', count(*) from public.diary_messages
union all select 'diary_assets', count(*) from public.diary_assets
union all select 'private_objects', count(*) from storage.objects where bucket_id = 'diary-private';
select count(*) as registered_members from auth.users
where is_anonymous is false and role = 'authenticated' and deleted_at is null;
select count(*) as confirmed_diaries, count(*) filter (where confirmation_fingerprint is null) as legacy_without_fingerprint
from public.diaries;
