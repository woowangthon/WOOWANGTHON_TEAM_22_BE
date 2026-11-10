"""정적 경계 검사. SQL parser/실제 PostgreSQL 검증을 대신하지 않는다."""
from pathlib import Path
import re

SQL = (Path(__file__).resolve().parents[1] / "supabase/candidates/comic_previews_v1.sql").read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL).lower()


def test_candidate_is_independent_and_preserves_existing_diary_assets_and_005():
    tables = {"comic_previews", "diary_comics", "comic_attach_requests"}
    assert set(re.findall(r"create table public\.(\w+)", CODE)) == tables
    assert set(re.findall(r"(?:alter table|insert into|update|delete from) public\.(\w+)", CODE)) <= tables
    assert "grant update (id) on public.diaries to service_role" in CODE
    assert "chat_sessions" not in CODE and "chat_requests" not in CODE
    assert not re.search(r"\b(drop|truncate|security definer)\b", CODE)
    assert "storage.objects" not in CODE


def test_server_write_and_member_read_permissions_are_separate():
    functions = re.findall(r"create function public\.(\w+)", CODE)
    assert CODE.count("security invoker set search_path = ''") == len(functions) == 4
    for name in functions:
        assert re.search(rf"revoke all on function public\.{name}\([^;]+from public, anon, authenticated", CODE)
        assert re.search(rf"grant execute on function public\.{name}\([^;]+to service_role", CODE)
    for name in ("comic_previews", "diary_comics", "comic_attach_requests"):
        assert f"alter table public.{name} enable row level security" in CODE
    assert "create policy comic_attach_requests" not in CODE


def test_attach_binds_confirmed_snapshot_and_serializes_request_receipts():
    attach = CODE.split("create function public.attach_diary_comic(")[1]
    assert attach.index("pg_advisory_xact_lock") < attach.index("insert into public.diary_comics")
    for check in ("d.confirmation_fingerprint is null", "d.source_session_id <> v.session_id",
                  "d.source_history_revision <> v.history_revision", "d.ai_draft->>'draft_id'",
                  "d.ai_draft->>'text'", "d.confirmed_text is distinct from v.draft_text",
                  "comic_not_ready", "idempotency_conflict", "comic_already_attached"):
        assert check in attach
    assert "primary key (user_id, client_request_id)" in CODE
    assert "references public.diaries(id, user_id)" in CODE
    assert "references public.comic_previews(preview_id, user_id)" in CODE
