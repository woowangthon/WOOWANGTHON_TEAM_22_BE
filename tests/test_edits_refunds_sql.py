"""SQL 후보 경계의 정적 검사. PostgreSQL 실행으로 표시하지 않는다."""
from pathlib import Path
import re

SQL = (Path(__file__).resolve().parents[1] / 'supabase/candidates/diary_edits_refunds_v04.sql').read_text(encoding='utf-8')
CODE = re.sub(r'--[^\n]*', '', SQL).lower()


def test_revision_and_soft_delete_preserve_original_asset_and_reward_foreign_keys():
    assert 'add column edit_version integer not null default 1' in CODE
    assert 'where deleted_at is null' in CODE
    assert 'drop constraint diaries_user_date_uq' in CODE
    for protected in ['diaries', 'diary_assets', 'diary_messages', 'member_daily_reward_grants']:
        assert not re.search(rf'delete from public\.{protected}\b', CODE)
    assert not re.search(r'update public\.diaries set ai_draft|set confirmation_fingerprint', CODE)


def test_date_grant_uniqueness_and_first_confirmed_includes_deleted_entitlement():
    assert 'unique(member_id, diary_date)' in CODE
    assert 'on conflict (member_id, diary_date) do nothing' in CODE
    assert 'select distinct on (diary_date)' in CODE
    assert 'order by diary_date, confirmed_at, id' in CODE
    assert 'where old.diary_id = d.id' in CODE
    grant = CODE.split("if p_action = 'sync_rewards' then")[1].split("elsif p_action = 'purchase'")[0]
    assert 'deleted_at is null' not in grant


def test_atomic_finalization_receipt_and_active_revision_and_server_only_access():
    assert 'unique(user_id, client_request_id)' in CODE
    assert 'references public.diary_edit_requests(request_id, diary_id, user_id)' in CODE
    command = CODE.split('create function public.member_diary_edit')[1].split('$function$;')[0]
    assert command.index('for update') < command.index("set state='completed'")
    assert command.index("r.state='completed'") < command.index('d.edit_version <> r.expected_version')
    assert 'active_drawing_edit_id=case when r.drawing is not null then r.request_id' in command
    assert "from public,anon,authenticated" in CODE and 'to service_role' in CODE
    assert 'security definer' not in CODE and not re.search(r'grant[^;]*(?:insert|update|delete)[^;]*to authenticated', CODE)


def test_purchase_refund_history_and_serial_wallet_mutations():
    assert 'create table public.shop_purchase_receipts' in CODE
    assert 'create table public.shop_refund_receipts' in CODE
    assert 'purchase_id uuid not null unique' in CODE
    assert 'unique(member_id, client_request_id)' in CODE
    assert not re.search(r'(?:update|delete from) public\.shop_(?:purchase|refund)_receipts', CODE)
    command = CODE.split('create or replace function public.member_room_command')[1]
    assert command.index('pg_advisory_xact_lock') < command.index('for update')
    assert 'coins=coins+v_purchase.price_paid' in command
    assert 'delete from public.member_room_placements' in command
    assert "detail='purchase_already_refunded'" in command


def test_live_row_rls_storage_and_comic_version_tracking():
    for name in ['diary_live_v04', 'messages_live_v04', 'assets_live_v04', 'comic_live_v04', 'drawing_storage_live_v04']:
        assert f'create policy {name}' in CODE
    for table in ['diary_edit_requests', 'diary_delete_requests', 'shop_purchase_receipts', 'shop_refund_receipts']:
        assert f'alter table public.{table} enable row level security' in CODE
    assert 'source_edit_version integer not null default 1' in CODE
    assert 'if d.edit_version <> 1 or d.confirmed_at is null' in CODE
    assert CODE.count("message='use edit api'") == 2


def test_candidate_has_no_seed_or_session_migration_and_preserves_old_drawing_uniqueness():
    assert not re.search(r'insert into public\.shop_items|\btruncate\b|\bcommit\b', CODE)
    assert 'public.chat_sessions' not in CODE
    assert 'diary_assets_scene_uq' not in CODE
    assert "'/original_drawing/'" in CODE
