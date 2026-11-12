-- READ ONLY: 01/02 적용 후 실행. 03은 BLOCKED라 v04의 installed=false가 현재 예상값.
-- 결과는 메타데이터/개수/해시만 포함. 서비스 RPC 및 사용자 행 출력 없음.
with expected_tables(stage, name) as (values ('room','member_reward_legacy_diaries'),
('room','member_reward_wallets'),
('room','member_daily_reward_grants'),
('room','shop_items'),
('room','member_owned_items'),
('room','member_room_placements'),
('room','member_room_settings'),
('room','member_tree_states'),
('room','member_tree_watering_events'),
('comic','comic_previews'),
('comic','diary_comics'),
('comic','comic_attach_requests'),
('v04','diary_edit_requests'),
('v04','diary_delete_requests'),
('v04','shop_purchase_receipts'),
('v04','shop_refund_receipts')),
table_status as (
 select e.stage, e.name, c.oid is not null as installed, c.relrowsecurity as rls,
   case when c.oid is not null then
     not (has_any_column_privilege('anon',c.oid,'INSERT,UPDATE')
       or has_table_privilege('anon',c.oid,'DELETE,TRUNCATE')
       or has_any_column_privilege('authenticated',c.oid,'INSERT,UPDATE')
       or has_table_privilege('authenticated',c.oid,'DELETE,TRUNCATE')) end as clients_cannot_write,
   case when c.oid is not null then has_table_privilege('service_role',c.oid,'SELECT') end as server_select,
   (select count(*) from pg_policy p where p.polrelid=c.oid) as policy_count
 from expected_tables e left join pg_class c on c.oid=to_regclass('public.'||e.name)
), expected_functions(stage, signature) as (values ('base','public.confirm_member_diary(uuid,text,text,integer,text,text,timestamp with time zone,jsonb,jsonb)'),
('base','public.reserve_diary_drawing(uuid,uuid,text,bigint,integer,integer,text)'),
('base','public.complete_diary_drawing(uuid,uuid,uuid)'),
('room','public.room_guard_owned_catalog()'),
('room','public.room_guard_item_usage()'),
('room','public.room_snapshot(uuid,date)'),
('room','public.member_room_command(uuid,text,jsonb)'),
('comic','public.comic_panels_valid(jsonb)'),
('comic','public.save_comic_preview_result(jsonb,text)'),
('comic','public.complete_comic_preview_asset(uuid,uuid,uuid)'),
('comic','public.attach_diary_comic(uuid,uuid,uuid,text)'),
('v04','public.member_diary_edit(uuid,uuid,text,jsonb)')),
function_status as (
 select e.stage,e.signature,p.oid is not null as installed,
   not p.prosecdef as security_invoker,
   case when p.oid is not null then
     not has_function_privilege('anon',p.oid,'EXECUTE')
     and not has_function_privilege('authenticated',p.oid,'EXECUTE')
     and has_function_privilege('service_role',p.oid,'EXECUTE') end as server_only,
   p.proconfig as settings
 from expected_functions e left join pg_proc p on p.oid=to_regprocedure(e.signature)
)
select jsonb_build_object(
 'tables', (select jsonb_agg(to_jsonb(t) order by stage,name) from table_status t),
 'functions', (select jsonb_agg(to_jsonb(f) order by stage,signature) from function_status f),
 'existing_data_fingerprint', jsonb_build_object('diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' order by t.id), ''))) from public.diaries t),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' order by t.id), ''))) from public.diary_messages t),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' order by t.id), ''))) from public.diary_assets t)),
 'diary_lock_column_grant', has_column_privilege('service_role','public.diaries','id','UPDATE'),
 'date_unique_constraint_present', exists(select 1 from pg_constraint
   where conrelid='public.diaries'::regclass and conname='diaries_user_date_uq' and contype='u'),
 'v04_policies', (select count(*) from pg_policy p where p.polname in
   ('diary_live_v04','messages_live_v04','assets_live_v04','edit_drawing_read_v04','comic_live_v04','drawing_storage_live_v04'))
) as verification;
