"""SQL 후보의 정적 안전 경계. 실제 PostgreSQL/RLS 통과를 의미하지 않습니다."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "supabase/migrations/20261109000500_persist_chat_sessions.sql"
SQL = CANDIDATE.read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL).lower()


def test_001_to_004_remain_byte_equivalent_to_head():
    paths = sorted((ROOT / "supabase/migrations").glob("*00[1-4]00_*.sql"))
    assert len(paths) == 4
    for path in paths:
        original = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}",
            "show", f"HEAD:supabase/migrations/{path.name}"], cwd=ROOT)
        assert original.replace(b"\r\n", b"\n") == path.read_bytes().replace(b"\r\n", b"\n")


def test_candidate_adds_only_two_tables_and_three_rpcs():
    assert re.findall(r"create table public\.(\w+)", CODE) == ["chat_sessions", "chat_requests"]
    assert re.findall(r"create function public\.(\w+)", CODE) == [
        "reserve_chat_request", "transition_chat_request", "confirm_chat_session_diary"]
    assert set(re.findall(r"(?:alter table|insert into|update) public\.(\w+)", CODE)) <= {"chat_sessions", "chat_requests"}
    assert not re.search(r"\b(delete\s+from|drop\s+|truncate\s+|commit\s*;)", CODE)
    assert "public.confirm_member_diary(" in CODE


def test_new_tables_and_functions_are_server_only():
    for table in ("chat_sessions", "chat_requests"):
        assert f"alter table public.{table} enable row level security" in CODE
    assert "from public, anon, authenticated, service_role" in CODE
    assert not re.search(r"grant[^;]*to\s+(?:public|anon|authenticated)\b", CODE)
    assert len(re.findall(r"security invoker set search_path = ''", CODE)) == 3
    for function in ("reserve_chat_request", "transition_chat_request", "confirm_chat_session_diary"):
        assert re.search(rf"revoke all on function public\.{function}\([^;]+from public,anon,authenticated", CODE)
        assert re.search(rf"grant execute on function public\.{function}\([^;]+to service_role", CODE)


def test_member_ownership_and_owner_immutability():
    assert CODE.count("u.is_anonymous is false and u.role = 'authenticated' and u.deleted_at is null") == 3
    assert "foreign key (session_id, owner_id)" in CODE
    assert "foreign key (active_request_id, session_id, owner_id)" in CODE
    assert "foreign key (confirmed_diary_id, owner_id)" in CODE
    for columns in re.findall(r"grant update\((.*?)\)", CODE, re.S):
        assert not {"owner_id", "session_id", "client_key", "fingerprint", "input", "confirmed_at"} & set(re.findall(r"\w+", columns))


def test_lock_order_for_confirmation_is_member_then_session_then_request():
    body = CODE.split("create function public.confirm_chat_session_diary(")[1]
    assert body.index("pg_advisory_xact_lock") < body.index("from public.chat_sessions") < body.index("from public.chat_requests")
    assert body.index("public.confirm_member_diary(") < body.index("set state = 'succeeded'") < body.index("set lifecycle = 'confirmed'")
    assert "r.confirmed_at,v_draft,v_messages" in body


def test_unknown_commits_as_returned_business_error_and_cannot_be_released_by_new_id():
    reserve = CODE.split("create function public.reserve_chat_request(")[1].split("create function public.transition_chat_request(")[0]
    assert reserve.index("a.dispatched_deadline <= v_now") < reserve.index("q.state = 'unknown'") < reserve.index("if r.id is null then\n        insert into public.chat_requests")
    assert "return pg_catalog.jsonb_build_object('error','ai_outcome_unknown')" in reserve
    transition = CODE.split("create function public.transition_chat_request(")[1].split("create function public.confirm_chat_session_diary(")[0]
    assert transition.index("r.dispatched_deadline <= v_now") < transition.index("if p_action = 'dispatched'")
    assert "r.state <> 'reserved' or r.lease_until <= v_now" in transition
    assert "s.version <> p_version" in transition and "r.attempt_token <> p_attempt_token" in transition
