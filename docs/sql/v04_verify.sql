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

-- 소급 보류 날짜에 새 ID로 지급된 행이 없어야 한다(0행).
select g.member_id,g.diary_date from public.member_daily_reward_grants g
where exists(select 1 from public.member_reward_legacy_diaries h
 join public.diaries d on d.id=h.diary_id and d.user_id=h.member_id
 where h.member_id=g.member_id and d.diary_date=g.diary_date);

-- 불변 구매/환불 원장 기준 현재 잔액 대조(0행). 실제 잔액 보정은 하지 않는다.
with earned as (
 select member_id,sum(coins) coins from public.member_daily_reward_grants group by member_id
), purchased as (
 select member_id,sum(price_paid) coins from public.shop_purchase_receipts group by member_id
), refunded as (
 select member_id,sum(refunded_coins) coins from public.shop_refund_receipts group by member_id
), members as (
 select member_id from public.member_reward_wallets union select member_id from earned
 union select member_id from purchased union select member_id from refunded
)
select m.member_id from members m
left join public.member_reward_wallets w using(member_id)
left join earned e using(member_id) left join purchased p using(member_id) left join refunded r using(member_id)
where coalesce(w.coins,0) <> coalesce(e.coins,0)-coalesce(p.coins,0)+coalesce(r.coins,0);

-- 네 신규 테이블 모두 일반 회원 직접 쓰기 false. 영수증은 서버 UPDATE/DELETE도 false.
select c.relname,r.role_name,
 has_table_privilege(r.role_name,c.oid,'INSERT') as direct_insert,
 has_any_column_privilege(r.role_name,c.oid,'UPDATE') as direct_update,
 has_table_privilege(r.role_name,c.oid,'DELETE') as direct_delete
from pg_class c join pg_namespace n on n.oid=c.relnamespace
cross join (values ('anon'),('authenticated'),('service_role')) r(role_name)
where n.nspname='public' and c.relname in
 ('diary_edit_requests','diary_delete_requests','shop_purchase_receipts','shop_refund_receipts');
