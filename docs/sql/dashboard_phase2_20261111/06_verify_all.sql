-- READ ONLY. 01 -> 02 -> 03_apply_v04 -> 04_apply_comic_jobs 완료 후.
-- date_unique_constraint_present=false, partial_date_unique_ok=true, duplicate groups=0.
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
('v04','shop_refund_receipts'),('comic_jobs','comic_generation_jobs')),
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
('v04','public.member_diary_edit(uuid,uuid,text,jsonb)'),('comic_jobs','public.comic_job_progress_valid(jsonb,jsonb)'),('comic_jobs','public.comic_job_command(text,jsonb)')),
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
 'postgres_role',current_user='postgres',
 'storage_bucket_private',exists(select 1 from storage.buckets where id='diary-private' and public is false),
 'tables', (select jsonb_agg(to_jsonb(t) order by stage,name) from table_status t),
 'functions', (select jsonb_agg(to_jsonb(f) order by stage,signature) from function_status f),
 'original_data_fingerprint',jsonb_build_object(
'diaries', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t) - array['edit_version','updated_at','deleted_at','active_drawing_edit_id'])::text) h from public.diaries t) rows),
'diary_messages', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_messages t) rows),
'diary_assets', (select jsonb_build_array(count(*), md5(coalesce(string_agg(h, '' order by h), ''))) from (select md5((to_jsonb(t))::text) h from public.diary_assets t) rows)),
 'diary_lock_column_grant', has_column_privilege('service_role','public.diaries','id','UPDATE'),
 'date_unique_constraint_present', exists(select 1 from pg_constraint
   where conrelid='public.diaries'::regclass and conname='diaries_user_date_uq' and contype='u'),
 'partial_date_unique_ok',(not exists(select 1 from pg_constraint where conrelid='public.diaries'::regclass
    and conname='diaries_user_date_uq')
and exists(select 1 from pg_index i where i.indexrelid=to_regclass('public.diaries_user_date_uq')
    and i.indrelid='public.diaries'::regclass and i.indisunique and i.indisvalid and i.indisready
    and i.indislive and i.indimmediate and i.indexprs is null
    and pg_get_expr(i.indpred,i.indrelid)='(deleted_at IS NULL)'
    and pg_get_indexdef(i.indexrelid)=
        'CREATE UNIQUE INDEX diaries_user_date_uq ON public.diaries USING btree (user_id, diary_date) WHERE (deleted_at IS NULL)')),
 'live_duplicate_groups',(select count(*) from (select 1 from public.diaries where deleted_at is null group by user_id,diary_date having count(*)>1) q),
 'reward_duplicate_groups',(select count(*) from (select 1 from public.member_daily_reward_grants group by member_id,diary_date having count(*)>1) q),
 'job_clients_cannot_read',not has_any_column_privilege('anon','public.comic_generation_jobs','SELECT') and not has_any_column_privilege('authenticated','public.comic_generation_jobs','SELECT'),
 'job_server_cannot_delete',not has_table_privilege('service_role','public.comic_generation_jobs','DELETE,TRUNCATE'),
 'v04_policies', (select count(*) from pg_policy p where p.polname in
   ('diary_live_v04','messages_live_v04','assets_live_v04','edit_drawing_read_v04','comic_live_v04','drawing_storage_live_v04'))
) as verification;
