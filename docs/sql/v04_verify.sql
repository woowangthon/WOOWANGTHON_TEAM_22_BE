-- REVIEW ONLY / READ ONLY. 아직 실행하지 않았다. 설치 직후 쓰기 재개 전에 검토한다.
select count(*) as invalid_diary_versions from public.diaries where edit_version < 1 or updated_at is null;
select count(*) as missing_purchase_receipts from public.member_owned_items o
 left join public.shop_purchase_receipts r on r.purchase_id=o.purchase_id
 where r.purchase_id is null or (r.member_id,r.item_id,r.price_paid,r.client_request_id)
 is distinct from (o.member_id,o.item_id,o.price_coins,o.purchase_request_id);
select count(*) as refunded_but_owned from public.member_owned_items o
 join public.shop_refund_receipts r on r.purchase_id=o.purchase_id;
select count(*) as invalid_active_drawings from public.diaries d
 join public.diary_edit_requests r on r.request_id=d.active_drawing_edit_id
 where r.diary_id<>d.id or r.user_id<>d.user_id or r.state<>'completed' or r.drawing is null;
select count(*) as receipt_amount_mismatches from public.shop_refund_receipts f
 join public.shop_purchase_receipts p on p.purchase_id=f.purchase_id
 where f.refunded_coins<>p.price_paid;
select count(*) as duplicate_reward_dates from (
 select member_id,diary_date from public.member_daily_reward_grants group by member_id,diary_date having count(*)>1
) d;
select c.relname,c.relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and c.relname in
 ('diary_edit_requests','diary_delete_requests','shop_purchase_receipts','shop_refund_receipts');
select role_name,has_function_privilege(role_name,'public.member_diary_edit(uuid,uuid,text,jsonb)','EXECUTE') as can_edit
 from unnest(array['anon','authenticated','service_role']) as r(role_name);
select role_name,has_table_privilege(role_name,'public.shop_purchase_receipts','UPDATE') as purchase_update,
 has_table_privilege(role_name,'public.shop_refund_receipts','DELETE') as refund_delete
 from unnest(array['anon','authenticated','service_role']) as r(role_name);
select tablename,policyname,permissive,roles from pg_policies
 where policyname like '%v04' order by tablename,policyname;
