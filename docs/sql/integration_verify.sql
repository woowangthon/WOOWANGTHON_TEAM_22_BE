-- 후보 적용 후 향후 승인된 조회 전용 검증. 지금 실행하지 않는다.
-- 설치 직후, 신규 테스트 전에 grants/wallets/purchases/events는 0건이어야 한다.
select 'legacy_hold' as resource, count(*) as rows from public.member_reward_legacy_diaries
union all select 'grants', count(*) from public.member_daily_reward_grants
union all select 'wallets', count(*) from public.member_reward_wallets
union all select 'purchases', count(*) from public.member_owned_items
union all select 'watering', count(*) from public.member_tree_watering_events;

-- 아래 무결성 조회는 모두 0행이어야 한다. 잔액은 수정하지 않는다.
select g.diary_id from public.member_daily_reward_grants g
join public.member_reward_legacy_diaries h using (diary_id);
select member_id, diary_id, count(*) from public.member_daily_reward_grants
group by member_id, diary_id having count(*) <> 1;
select g.diary_id from public.member_daily_reward_grants g
join public.diaries d on d.id = g.diary_id
where g.member_id <> d.user_id or d.confirmation_fingerprint is null;
with earned as (
    select member_id, sum(coins) coins, sum(water_drops) water
    from public.member_daily_reward_grants group by member_id
), spent as (
    select member_id, sum(price_coins) coins from public.member_owned_items group by member_id
), watered as (
    select member_id, sum(water_drops_spent) water, sum(growth_points_added) growth
    from public.member_tree_watering_events group by member_id
), members as (
    select member_id from public.member_reward_wallets union select member_id from earned
    union select member_id from spent union select member_id from watered
)
select m.member_id from members m
left join public.member_reward_wallets w using (member_id)
left join earned e using (member_id) left join spent s using (member_id)
left join watered t using (member_id) left join public.member_tree_states g using (member_id)
where coalesce(w.coins, 0) <> coalesce(e.coins, 0) - coalesce(s.coins, 0)
   or coalesce(w.water_drops, 0) <> coalesce(e.water, 0) - coalesce(t.water, 0)
   or coalesce(g.growth_points, 0) <> coalesce(t.growth, 0);

-- v0.4 날짜별 자격과 일치시킨다. 삭제/재작성 ID를 미지급으로 중복 계산하지 않는다.
-- 삭제된 최초 확정의 지연 지급은 포함하고, fingerprint 없는 과거 보류 날짜도 제외한다.
with first_confirmed as (
    select distinct on (user_id,diary_date) id,user_id,diary_date
    from public.diaries where confirmation_fingerprint is not null
    order by user_id,diary_date,confirmed_at,id
)
select count(*) as pending_new_confirmations from first_confirmed d
where not exists (select 1 from public.member_reward_legacy_diaries h
    join public.diaries original on original.id=h.diary_id and original.user_id=h.member_id
    where h.member_id=d.user_id and original.diary_date=d.diary_date)
and not exists (select 1 from public.member_daily_reward_grants g
    where g.member_id=d.user_id and g.diary_date=d.diary_date);

select c.relname, c.relrowsecurity, c.relacl from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in (
    'member_reward_legacy_diaries','member_reward_wallets','member_daily_reward_grants','shop_items',
    'member_owned_items','member_room_placements','member_room_settings','member_tree_states',
    'member_tree_watering_events','comic_previews','diary_comics','comic_attach_requests');
select p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef, p.proconfig,
    has_function_privilege('anon', p.oid, 'EXECUTE') as anon_execute,
    has_function_privilege('authenticated', p.oid, 'EXECUTE') as member_execute,
    has_function_privilege('service_role', p.oid, 'EXECUTE') as server_execute
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in (
    'member_room_command','room_snapshot','room_guard_owned_catalog','room_guard_item_usage',
    'comic_panels_valid','save_comic_preview_result','complete_comic_preview_asset','attach_diary_comic');
select has_column_privilege('service_role','public.diaries','id','UPDATE') as comic_lock_permission;
select c.relname, r.role_name,
    has_table_privilege(r.role_name, c.oid, 'INSERT') as direct_insert,
    has_any_column_privilege(r.role_name, c.oid, 'UPDATE') as direct_update,
    has_table_privilege(r.role_name, c.oid, 'DELETE') as direct_delete
from pg_class c join pg_namespace n on n.oid = c.relnamespace
cross join (values ('anon'), ('authenticated')) r(role_name)
where n.nspname = 'public' and c.relname in (
    'member_reward_legacy_diaries','member_reward_wallets','member_daily_reward_grants','shop_items',
    'member_owned_items','member_room_placements','member_room_settings','member_tree_states',
    'member_tree_watering_events','comic_previews','diary_comics','comic_attach_requests');
select id, public from storage.buckets where id = 'diary-private';
