-- REVIEW ONLY / READ ONLY. 현재 실행하지 않았다. 기존 통합 점검과 함께 검토한다.
-- v0.4 객체가 이미 있으면 설치를 중단하고 적용 이력부터 확인한다.
select to_regclass('public.diary_edit_requests') as edit_requests,
       to_regclass('public.diary_delete_requests') as delete_requests,
       to_regclass('public.shop_purchase_receipts') as purchases,
       to_regclass('public.shop_refund_receipts') as refunds;
select table_name, column_name, data_type from information_schema.columns
 where table_schema='public' and table_name in ('diaries','member_owned_items','diary_comics')
 order by table_name, ordinal_position;
select conrelid::regclass as table_name, conname, pg_get_constraintdef(oid) as definition
 from pg_constraint where conrelid in ('public.diaries'::regclass,'public.member_owned_items'::regclass,
 'public.member_daily_reward_grants'::regclass,'public.member_room_placements'::regclass,
 'public.member_room_settings'::regclass) order by table_name, conname;
-- 0이어야 한다. 중복을 삭제하거나 지갑을 바꿔 설치를 강행하지 않는다.
select count(*) as duplicate_reward_dates from (
 select member_id,diary_date from public.member_daily_reward_grants group by member_id,diary_date having count(*) > 1
) d;
select count(*) as active_purchase_rows from public.member_owned_items;
select tablename,policyname,permissive,roles,qual from pg_policies
 where schemaname in ('public','storage') and tablename in
 ('diaries','diary_messages','diary_assets','diary_comics','objects') order by tablename,policyname;
select id,public from storage.buckets where id='diary-private';
select proname,prosecdef,proconfig from pg_proc where pronamespace='public'::regnamespace
 and proname in ('confirm_member_diary','reserve_diary_drawing','complete_diary_drawing',
                 'room_snapshot','member_room_command','attach_diary_comic');
