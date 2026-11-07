import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "tests/sql"
PRODUCTION = SQL_DIR / "verify_confirm_member_diary_production.sql"
LOCAL = SQL_DIR / "verify_confirm_member_diary.sql"


def test_all_applied_migrations_preserved():
    for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
        original = subprocess.check_output(
            ["git", "show", f"b7e1335:supabase/migrations/{path.name}"], cwd=ROOT,
        ).replace(b"\r\n", b"\n")
        assert path.read_bytes().replace(b"\r\n", b"\n") == original


def test_production_rpc_source_matches_reviewed_migration():
    migration = (ROOT / "supabase/migrations/20261107000300_confirm_member_diary.sql").read_text(
        encoding="utf-8",
    )
    source = migration.split("as $function$", 1)[1].split("$function$;", 1)[0]
    digest = hashlib.md5(source.strip(" ").encode("utf-8")).hexdigest()
    assert f"= '{digest}'" in PRODUCTION.read_text(encoding="utf-8")


@pytest.mark.parametrize("path,gate", [(LOCAL, "v_disposable_local"), (PRODUCTION, "v_reviewed")])
def test_verification_requires_review_and_rolls_back_inside_do(path, gate):
    sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8")).lower()
    assert f"{gate} boolean := false;" in sql
    assert sql.index(f"if not {gate}") < sql.index("perform public.confirm_member_diary")
    assert sql.strip().startswith("begin;") and sql.strip().endswith("rollback;")
    assert not re.search(r"\b(commit|delete|update|truncate|grant|revoke)\b", sql)
    assert "set local statement_timeout = '30s'" in sql
    assert "set local idle_in_transaction_session_timeout = '60s'" in sql
    marker = sql.index("raise exception using errcode = 'zv001'")
    handler = sql.index("exception when sqlstate 'zv001' then")
    result = sql.index("perform pg_catalog.set_config('rpc_verification.result'")
    assert sql.index("perform public.confirm_member_diary") < marker < handler < result
    assert "exception when insufficient_privilege then" in sql
    assert "execute 'set local role service_role'" in sql


def test_production_scope_and_failure_evidence():
    sql = PRODUCTION.read_text(encoding="utf-8")
    reads = (ROOT / "scripts/verify_diary_reads.py").read_text(encoding="utf-8")
    fixtures = re.findall(r"where id = '([0-9a-f-]{36})' and diary_date", sql)
    assert len(fixtures) == 2 and all(fixture in reads for fixture in fixtures)
    assert "date '2026-11-07'" in sql
    assert "date '2035-01-02', date '2035-01-03'" in sql
    assert not re.search(r"\binsert\s+into\b", sql, re.IGNORECASE)
    assert "v_before is distinct from v_after" in sql
    assert "v_after is distinct from v_saved" in sql
    assert "not tgisinternal and tgenabled <> 'D'" in sql
    assert "diary_messages_text_ck" in sql and "diaries_ai_draft_ck" in sql
    assert "v_detail = 'IDEMPOTENCY_CONFLICT'" in sql
    assert "v_detail = 'DIARY_DATE_CONFLICT'" in sql
