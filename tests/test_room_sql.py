"""후보의 정적 회귀 경계만 검사한다. 실제 PostgreSQL 실행 결과가 아니다."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "supabase/candidates/room_rewards_v031.sql"
CODE = re.sub(r"--[^\n]*", "", CANDIDATE.read_text(encoding="utf-8")).lower()
TABLES = {
    "member_reward_wallets", "member_daily_reward_grants", "shop_items", "member_owned_items",
    "member_room_placements", "member_room_settings", "member_tree_states", "member_tree_watering_events",
    "member_reward_legacy_diaries",
}


def test_room_candidate_is_separate_from_005_and_does_not_change_existing_tables_or_rpcs():
    assert CANDIDATE.parent.name == "candidates"
    assert set(re.findall(r"create table public\.(\w+)", CODE)) == TABLES
    assert set(re.findall(r"(?:alter table|insert into|update|delete from) public\.(\w+)", CODE)) <= TABLES
    assert not re.search(r"\b(drop|truncate|commit|security definer)\b", CODE)
    assert "public.diaries d" in CODE
    assert "public.confirm_member_diary(" not in CODE
    assert not re.search(r"insert into public\.shop_items", CODE)


def test_member_read_rls_and_server_only_rpc_write_boundaries():
    for table in TABLES:
        assert f"alter table public.{table} enable row level security" in CODE
        if table != "member_reward_legacy_diaries":
            assert re.search(rf"create policy \w+ on public\.{table} for select to authenticated", CODE)
    assert not re.search(r"create policy \w+ on public.member_reward_legacy_diaries", CODE)
    assert CODE.count("(select auth.jwt()->>'is_anonymous') = 'false'") == 8
    assert CODE.count("member_id = (select auth.uid())") == 8
    assert not re.search(r"grant\s+(?:insert|update|delete)[^;]*to authenticated", CODE)
    functions = re.findall(r"create function public\.(\w+)", CODE)
    assert len(functions) == 4
    assert CODE.count("security invoker set search_path = ''") == len(functions)
    for function in functions:
        assert re.search(rf"revoke all on function public\.{function}\([^;]+from public, anon, authenticated", CODE)
        assert re.search(rf"grant execute on function public\.{function}\([^;]+to service_role", CODE)


def test_database_constraints_cover_nonnegative_unique_ownership_and_growth():
    for expression in ("check (coins >= 0)", "check (water_drops >= 0)",
                       "primary key (member_id, diary_id)", "unique (member_id, client_request_id)",
                       "unique (member_id, purchase_request_id)",
                       "unique (member_id, watered_on)", "check (growth_points between 0 and 7)"):
        assert expression in CODE
    assert CODE.count("references public.member_owned_items(member_id, item_id)") == 2
    assert "foreign key (diary_id, member_id) references public.diaries(id, user_id)" in CODE
    assert "create trigger room_placement_usage_guard" in CODE and "create trigger room_theme_usage_guard" in CODE
    assert "owned item geometry is immutable" in CODE
    assert "grant update (item_id) on public.shop_items to service_role" in CODE


def test_member_lock_precedes_seoul_clock_wallet_and_all_mutations():
    command = CODE.split("create function public.member_room_command(")[1]
    assert command.index("pg_advisory_xact_lock") < command.index("v_now := pg_catalog.clock_timestamp()")
    assert command.index("v_now := pg_catalog.clock_timestamp()") < command.index("insert into public.member_reward_wallets")
    assert "(v_now at time zone 'asia/seoul')::date" in command
    assert "u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null" in command
    assert "for update" in command and "for share" in command


def test_frozen_legacy_ids_prevent_backfill_and_only_new_diary_grants_increment_wallet():
    assert CODE.index("lock table public.diaries in share mode") < CODE.index("select id, user_id from public.diaries")
    assert "where old.diary_id = d.id" in CODE
    assert "d.confirmation_fingerprint is not null" in CODE
    assert "on conflict (member_id, diary_id) do nothing" in CODE
    assert "returning diary_date, coins, water_drops" in CODE
    assert "coins = coins + v_count::bigint * 30" in CODE


def test_request_replay_precedes_charging_and_adult_tree_ignores_theme_until_seven():
    command = CODE.split("create function public.member_room_command(")[1]
    assert command.index("o.purchase_request_id = p_payload->>'client_request_id'") < command.index("coins = coins - v_item.price_coins")
    assert command.index("e.client_request_id = p_payload->>'client_request_id'") < command.index("water_drops = water_drops - 1")
    assert command.index("if v_stage >= 7") < command.index("water_drops = water_drops - 1")
    assert "case when coalesce(g.growth_points, 0) < 7" in CODE
    assert "else coalesce(t.adult_tree_asset_key, 'tree_adult_default') end" in CODE
