import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"


def sql_text():
    text = (MIGRATIONS / "20261107000300_confirm_member_diary.sql").read_text(encoding="utf-8")
    return re.sub(r"--[^\n]*", "", text).lower()


def test_existing_migrations_unchanged():
    for name in ("20261107000100_create_member_diaries.sql",
                 "20261107000200_private_diary_storage_policies.sql"):
        original = subprocess.check_output(
            ["git", "show", f"ab82b8a:supabase/migrations/{name}"], cwd=ROOT,
        ).replace(b"\r\n", b"\n")
        assert (MIGRATIONS / name).read_bytes().replace(b"\r\n", b"\n") == original


def test_rpc_roles_and_no_member_write_grants():
    sql = sql_text()
    signature = r"public\.confirm_member_diary\(\s*uuid, text, text, integer, text, text, timestamptz, jsonb, jsonb\s*\)"
    assert re.search(r"revoke all on function " + signature + r" from public, anon, authenticated", sql)
    assert re.search(r"grant execute on function " + signature + r" to service_role", sql)
    assert "security invoker" in sql and "security definer" not in sql
    assert "set search_path = ''" in sql
    for statement in re.findall(r"grant\b[^;]+;", sql):
        assert re.search(r"to service_role\s*;", statement)
    assert not re.search(r"(?:create|alter|drop)\s+policy|disable\s+row\s+level\s+security", sql)
    assert "delete from" not in sql and "update public." not in sql


def test_rpc_transaction_and_persistent_idempotency_guards():
    sql = sql_text()
    assert "on public.diaries (user_id, source_client_request_id)" in sql
    assert "where confirmation_fingerprint is not null" in sql
    assert "pg_catalog.pg_advisory_xact_lock" in sql
    assert sql.index("pg_catalog.pg_advisory_xact_lock") < sql.index("insert into public.diaries")
    assert "v_existing.confirmation_fingerprint <> p_fingerprint" in sql
    assert "errcode = 'pt409'" in sql and "errcode = 'pt410'" in sql
    assert "insert into public.diaries" in sql and "insert into public.diary_messages" in sql
    assert "p_ai_draft ->> 'text'" in sql
    assert "(p_confirmed_at at time zone 'asia/seoul')::date" in sql
    assert "with ordinality" in sql and "m.ordinal - 1" in sql
    assert "from auth.users" in sql and "u.is_anonymous is false" in sql
    assert not re.search(r"\b(commit|rollback)\b", sql)
    assert not re.search(r"exception\s+when\s+others|when\s+.+then\s+null", sql)
    assert "exception when unique_violation" in sql and "raise;" in sql
