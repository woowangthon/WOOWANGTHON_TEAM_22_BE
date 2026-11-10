-- 로컬 검토 후보. 운영 미적용 / 표준 migrations 경로에 복사하지 말 것.
-- 선행: 001~004. 세션 영속화 005와 무관하며 기존 일기·RPC를 수정하지 않는다.
-- v0.3.1: 1440×900 무대, 좌상단 정수 px, 기본 에셋은 계약 식별자만 사용한다.
-- 상품 seed 없음. 테마 에셋은 승인된 카탈로그에서 공급한다.
-- 반드시 하나의 READ COMMITTED 트랜잭션으로 설치한다. 아래 잠금/스냅샷 분리 실행 금지.
-- 날짜 경계가 아닌 기존 ID 보류 목록: 소급 결정 전 기존 일기에 지급하지 않는다.
lock table public.diaries in share mode;
create table public.member_reward_legacy_diaries (
    diary_id uuid primary key,
    member_id uuid not null,
    foreign key (diary_id, member_id) references public.diaries(id, user_id) on delete restrict
);
insert into public.member_reward_legacy_diaries(diary_id, member_id)
    select id, user_id from public.diaries;
alter table public.member_reward_legacy_diaries enable row level security;
revoke all on public.member_reward_legacy_diaries from public, anon, authenticated, service_role;
grant select on public.member_reward_legacy_diaries to service_role;

create table public.member_reward_wallets (
    member_id uuid primary key references auth.users(id) on delete restrict,
    coins bigint not null default 0 check (coins >= 0),
    water_drops bigint not null default 0 check (water_drops >= 0),
    updated_at timestamptz not null default now()
);

create table public.member_daily_reward_grants (
    member_id uuid not null references auth.users(id) on delete restrict,
    diary_date date not null,
    diary_id uuid not null,
    coins integer not null default 30 check (coins = 30),
    water_drops integer not null default 1 check (water_drops = 1),
    granted_at timestamptz not null default now(),
    primary key (member_id, diary_id),
    foreign key (diary_id, member_id) references public.diaries(id, user_id) on delete restrict
);

create table public.shop_items (
    item_id text primary key check (item_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    name text not null check (char_length(name) between 1 and 200 and name ~ '[^[:space:]]'),
    category text not null check (category in ('furniture', 'decoration', 'theme')),
    price_coins bigint not null check (price_coins >= 0),
    asset_key text,
    width integer,
    height integer,
    background_asset_key text,
    diary_cover_asset_key text,
    adult_tree_asset_key text,
    is_active boolean not null default false,
    constraint shop_items_assets_ck check (
        (asset_key is null or char_length(asset_key) between 1 and 256)
        and (background_asset_key is null or char_length(background_asset_key) between 1 and 256)
        and (diary_cover_asset_key is null or char_length(diary_cover_asset_key) between 1 and 256)
        and (adult_tree_asset_key is null or char_length(adult_tree_asset_key) between 1 and 256)
    ),
    constraint shop_items_shape_ck check (
        (category in ('furniture', 'decoration') and asset_key is not null
            and width is not null and width between 1 and 1440
            and height is not null and height between 1 and 900
            and background_asset_key is null and diary_cover_asset_key is null and adult_tree_asset_key is null)
        or (category = 'theme' and asset_key is null and width is null and height is null
            and background_asset_key is not null and diary_cover_asset_key is not null
            and adult_tree_asset_key is not null)
    )
);

create table public.member_owned_items (
    member_id uuid not null references auth.users(id) on delete restrict,
    item_id text not null references public.shop_items(item_id) on delete restrict,
    price_coins bigint not null check (price_coins >= 0),
    purchase_request_id text not null check (purchase_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    purchased_at timestamptz not null default now(),
    primary key (member_id, item_id),
    unique (member_id, purchase_request_id)
);

create table public.member_room_placements (
    member_id uuid not null,
    item_id text not null,
    x integer not null check (x >= 0 and x <= 1440),
    y integer not null check (y >= 0 and y <= 900),
    updated_at timestamptz not null default now(),
    primary key (member_id, item_id),
    foreign key (member_id, item_id) references public.member_owned_items(member_id, item_id) on delete restrict
);

create table public.member_room_settings (
    member_id uuid primary key references auth.users(id) on delete restrict,
    active_theme_item_id text,
    updated_at timestamptz not null default now(),
    foreign key (member_id, active_theme_item_id)
        references public.member_owned_items(member_id, item_id) on delete restrict
);

create table public.member_tree_states (
    member_id uuid primary key references auth.users(id) on delete restrict,
    growth_points integer not null default 0 check (growth_points between 0 and 7),
    updated_at timestamptz not null default now()
);

create table public.member_tree_watering_events (
    event_id uuid primary key default gen_random_uuid(),
    member_id uuid not null references auth.users(id) on delete restrict,
    client_request_id text not null check (client_request_id ~ '^[A-Za-z0-9_-]{1,128}$'),
    watered_on date not null,
    water_drops_spent integer not null default 1 check (water_drops_spent = 1),
    growth_points_added integer not null default 1 check (growth_points_added = 1),
    created_at timestamptz not null default now(),
    unique (member_id, client_request_id),
    unique (member_id, watered_on)
);

alter table public.member_reward_wallets enable row level security;
alter table public.member_daily_reward_grants enable row level security;
alter table public.shop_items enable row level security;
alter table public.member_owned_items enable row level security;
alter table public.member_room_placements enable row level security;
alter table public.member_room_settings enable row level security;
alter table public.member_tree_states enable row level security;
alter table public.member_tree_watering_events enable row level security;

revoke all privileges on table public.member_reward_wallets, public.member_daily_reward_grants,
    public.shop_items, public.member_owned_items, public.member_room_placements,
    public.member_room_settings, public.member_tree_states, public.member_tree_watering_events
    from public, anon, authenticated;
grant select on table public.member_reward_wallets, public.member_daily_reward_grants,
    public.shop_items, public.member_owned_items, public.member_room_placements,
    public.member_room_settings, public.member_tree_states, public.member_tree_watering_events
    to authenticated;
grant select, insert, update on table public.member_reward_wallets, public.member_daily_reward_grants,
    public.member_owned_items, public.member_room_placements, public.member_room_settings,
    public.member_tree_states, public.member_tree_watering_events to service_role;
grant delete on table public.member_room_placements to service_role;
grant select on table public.shop_items to service_role;
-- PostgreSQL의 SELECT ... FOR SHARE에는 적어도 한 열의 UPDATE 권한도 필요하다.
grant update (item_id) on public.shop_items to service_role;

create policy reward_wallets_select_own on public.member_reward_wallets for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy reward_grants_select_own on public.member_daily_reward_grants for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy owned_items_select_own on public.member_owned_items for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy placements_select_own on public.member_room_placements for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy room_settings_select_own on public.member_room_settings for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy tree_states_select_own on public.member_tree_states for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy watering_events_select_own on public.member_tree_watering_events for select to authenticated
    using (member_id = (select auth.uid()) and (select auth.jwt()->>'is_anonymous') = 'false');
create policy shop_items_select_visible on public.shop_items for select to authenticated
    using ((select auth.jwt()->>'is_anonymous') = 'false' and (is_active or exists (
        select 1 from public.member_owned_items o where o.member_id = (select auth.uid())
            and o.item_id = shop_items.item_id
    )));

-- 보유 카탈로그의 크기/종류는 고정: 사후 수정으로 방 경계를 벗어나거나 테마가 가구로 바뀌지 않게 한다.
create function public.room_guard_owned_catalog() returns trigger
language plpgsql security invoker set search_path = ''
as $function$
begin
    if (old.category, old.width, old.height) is distinct from (new.category, new.width, new.height)
        and exists (select 1 from public.member_owned_items o where o.item_id = old.item_id) then
        raise exception using errcode = '22023', message = 'owned item geometry is immutable';
    end if;
    return new;
end;
$function$;
create trigger room_owned_catalog_guard before update on public.shop_items
    for each row execute function public.room_guard_owned_catalog();

-- 복합 FK의 보유권 검사에 더해 직접 DB 쓰기도 종류/크기 규칙을 따르게 한다.
create function public.room_guard_item_usage() returns trigger
language plpgsql security invoker set search_path = ''
as $function$
declare
    v_item public.shop_items%rowtype;
    v_item_id text;
begin
    if tg_table_name = 'member_room_settings' then
        v_item_id := new.active_theme_item_id;
        if v_item_id is null then return new; end if;
    else
        v_item_id := new.item_id;
    end if;
    select i.* into v_item from public.shop_items i where i.item_id = v_item_id for share;
    if not found then
        raise exception using errcode = 'PT404', message = 'item not found', detail = 'ITEM_NOT_FOUND';
    end if;
    if tg_table_name = 'member_room_settings' then
        if v_item.category <> 'theme' then
            raise exception using errcode = 'PT404', message = 'theme not found', detail = 'ITEM_NOT_FOUND';
        end if;
    elsif v_item.category not in ('furniture', 'decoration') then
        raise exception using errcode = 'PT422', message = 'not placeable', detail = 'ITEM_NOT_PLACEABLE';
    elsif new.x < 0 or new.y < 0 or new.x + v_item.width > 1440 or new.y + v_item.height > 900 then
        raise exception using errcode = 'PT422', message = 'invalid position', detail = 'ROOM_POSITION_INVALID';
    end if;
    return new;
end;
$function$;
create trigger room_placement_usage_guard before insert or update on public.member_room_placements
    for each row execute function public.room_guard_item_usage();
create trigger room_theme_usage_guard before insert or update on public.member_room_settings
    for each row execute function public.room_guard_item_usage();

-- 단일 SQL 문으로 방·지갑·나무를 구성한다. 호출은 아래 서버 전용 명령 함수로 제한한다.
create function public.room_snapshot(p_member_id uuid, p_on date) returns jsonb
language sql stable security invoker set search_path = ''
as $function$
    select pg_catalog.jsonb_build_object(
        'as_of_date', p_on,
        'wallet', pg_catalog.jsonb_build_object(
            'coins', coalesce(w.coins, 0), 'water_drops', coalesce(w.water_drops, 0)),
        'room', pg_catalog.jsonb_build_object('width', 1440, 'height', 900,
            'theme', pg_catalog.jsonb_build_object('item_id', s.active_theme_item_id,
                'background_asset_key', coalesce(t.background_asset_key, 'room_default'),
                'diary_cover_asset_key', coalesce(t.diary_cover_asset_key, 'cover_default'))),
        'tree', pg_catalog.jsonb_build_object('stage', coalesce(g.growth_points, 0), 'max_stage', 7,
            'days_watered', coalesce(g.growth_points, 0),
            'watered_today', exists (select 1 from public.member_tree_watering_events e
                where e.member_id = p_member_id and e.watered_on = p_on),
            'asset_key', case when coalesce(g.growth_points, 0) < 7
                then 'tree_stage_' || coalesce(g.growth_points, 0)::text
                else coalesce(t.adult_tree_asset_key, 'tree_adult_default') end),
        'inventory', coalesce((select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                'item_id', i.item_id, 'name', i.name, 'category', i.category,
                'asset_key', i.asset_key, 'width', i.width, 'height', i.height,
                'position', case when p.item_id is null then null
                    else pg_catalog.jsonb_build_object('x', p.x, 'y', p.y) end) order by i.item_id)
            from public.member_owned_items o join public.shop_items i on i.item_id = o.item_id
            left join public.member_room_placements p on p.member_id = o.member_id and p.item_id = o.item_id
            where o.member_id = p_member_id), '[]'::jsonb)
    )
    from (values (p_member_id)) as m(member_id)
    left join public.member_reward_wallets w on w.member_id = m.member_id
    left join public.member_room_settings s on s.member_id = m.member_id
    left join public.shop_items t on t.item_id = s.active_theme_item_id
    left join public.member_tree_states g on g.member_id = m.member_id;
$function$;

create function public.member_room_command(
    p_member_id uuid, p_action text, p_payload jsonb
) returns jsonb
language plpgsql security invoker set search_path = ''
as $function$
declare
    v_now timestamptz;
    v_today date;
    v_keys text[];
    v_item public.shop_items%rowtype;
    v_owned public.member_owned_items%rowtype;
    v_event public.member_tree_watering_events%rowtype;
    v_wallet public.member_reward_wallets%rowtype;
    v_state jsonb;
    v_rewards jsonb;
    v_count integer;
    v_stage integer;
    v_x integer;
    v_y integer;
begin
    if p_member_id is null or not exists (select 1 from auth.users u where u.id = p_member_id
        and u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null) then
        raise exception using errcode = '22023', message = 'invalid member';
    end if;
    if p_action is null or p_action not in ('get_room', 'sync_rewards') then
        raise exception using errcode = '22023', message = 'invalid room action';
    end if;
    v_keys := array[]::text[];
    if p_payload is null or pg_catalog.jsonb_typeof(p_payload) <> 'object'
        or not (p_payload ?& v_keys) or (p_payload - v_keys) <> '{}'::jsonb then
        raise exception using errcode = '22023', message = 'invalid room payload';
    end if;
    if p_payload ? 'client_request_id' and (pg_catalog.jsonb_typeof(p_payload->'client_request_id') <> 'string'
        or (p_payload->>'client_request_id') !~ '^[A-Za-z0-9_-]{1,128}$') then
        raise exception using errcode = '22023', message = 'invalid request id';
    end if;
    if p_payload ? 'item_id' and not (p_action = 'theme' and p_payload->'item_id' = 'null'::jsonb)
        and (pg_catalog.jsonb_typeof(p_payload->'item_id') <> 'string'
            or (p_payload->>'item_id') !~ '^[A-Za-z0-9_-]{1,128}$') then
        raise exception using errcode = '22023', message = 'invalid item id';
    end if;

    -- 모든 명령이 같은 회원 잠금을 사용하여 구매/지급/물 사용 간 잔액 경합도 직렬화한다.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_member_id::text, 22));
    -- 잠금을 기다리는 동안 서울 자정이 지났다면 실제 처리일을 사용한다.
    v_now := pg_catalog.clock_timestamp();
    v_today := (v_now at time zone 'Asia/Seoul')::date;
    if p_action = 'get_room' then
        return public.room_snapshot(p_member_id, v_today);
    end if;

    insert into public.member_reward_wallets(member_id) values (p_member_id) on conflict do nothing;
    select w.* into v_wallet from public.member_reward_wallets w where w.member_id = p_member_id for update;

    if p_action = 'sync_rewards' then
        with granted as (
            insert into public.member_daily_reward_grants(member_id, diary_date, diary_id, granted_at)
            select p_member_id, d.diary_date, d.id, v_now from public.diaries d
            where d.user_id = p_member_id and d.confirmation_fingerprint is not null
                and not exists (select 1 from public.member_reward_legacy_diaries old
                    where old.diary_id = d.id)
            on conflict (member_id, diary_id) do nothing
            returning diary_date, coins, water_drops
        ) select count(*)::integer, coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'diary_date', diary_date, 'coins', coins, 'water_drops', water_drops) order by diary_date), '[]'::jsonb)
            into v_count, v_rewards from granted;
        update public.member_reward_wallets set coins = coins + v_count::bigint * 30,
            water_drops = water_drops + v_count, updated_at = v_now where member_id = p_member_id;
        v_state := public.room_snapshot(p_member_id, v_today);
        return pg_catalog.jsonb_build_object('new_rewards', v_rewards, 'wallet', v_state->'wallet');
    end if;
    raise exception using errcode = '22023', message = 'unhandled room action';
end;
$function$;

revoke all on function public.room_guard_owned_catalog() from public, anon, authenticated;
revoke all on function public.room_guard_item_usage() from public, anon, authenticated;
revoke all on function public.room_snapshot(uuid, date) from public, anon, authenticated;
revoke all on function public.member_room_command(uuid, text, jsonb) from public, anon, authenticated;
grant execute on function public.room_guard_owned_catalog() to service_role;
grant execute on function public.room_guard_item_usage() to service_role;
grant execute on function public.room_snapshot(uuid, date) to service_role;
grant execute on function public.member_room_command(uuid, text, jsonb) to service_role;
