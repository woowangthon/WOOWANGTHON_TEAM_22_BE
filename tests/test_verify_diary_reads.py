"""원격 연결 없는 BE 조회 검증 도구 검사."""

import json
import logging
from types import SimpleNamespace
import warnings

import httpx
import pytest
from starlette.responses import JSONResponse

from app.config import Settings
from scripts import verify_diary_reads as reads
from tests.conftest import MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from tests.test_saved_diaries import FakeDatabase

EMAILS = ("fake-a@example.invalid", "fake-b@example.invalid")
PASSWORDS = ("fake-password-a", "fake-password-b")
TOKENS = ("fake-member-a", "fake-member-b")
REFRESH = "fake-refresh-private"
TEXT = "가상 비공개 일기와 대화"
SENSITIVE = (*EMAILS, *PASSWORDS, *TOKENS, REFRESH, TEXT, TEST_PUBLISHABLE_KEY)
SETTINGS = Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY)
CREDENTIALS = tuple(reads.Credentials(email, password) for email, password in zip(EMAILS, PASSWORDS))


class FakeSupabase:
    def __init__(self, override=None):
        self.override = override
        self.requests = []
        self.database = FakeDatabase()
        for diary_id, owner in ((reads.DIARY_A, MEMBER_A), (reads.DIARY_B, MEMBER_B)):
            self.database.diaries[diary_id] = {
                "id": diary_id, "user_id": str(owner), "diary_date": reads.DIARY_DATE,
                "weather": None, "confirmed_text": TEXT,
                "confirmed_at": "2026-11-07T00:00:00Z",
                "ai_draft": {"draft_id": "draft_" + "a" * 32, "text": TEXT,
                             "pages": [{"text": TEXT, "source_ids": [1], "imageUrl": None}],
                             "sources": [{"id": 1, "text": TEXT}],
                             "mock": True, "fallback": True},
            }
            self.database.messages[diary_id] = [{"position": 0, "role": "child", "text": TEXT}]

    @staticmethod
    def user(index):
        return {"id": str((MEMBER_A, MEMBER_B)[index]), "is_anonymous": False,
                "role": "authenticated", "aud": "authenticated"}

    async def __call__(self, request):
        self.requests.append(request)
        assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
        assert not request.headers.get("cookie")
        if self.override:
            result = self.override(request)
            if result is not None:
                return result
        if request.url.path == "/auth/v1/token":
            assert request.method == "POST" and "authorization" not in request.headers
            assert request.url.params["grant_type"] == "password"
            payload = json.loads(request.content)
            index = EMAILS.index(payload["email"])
            assert payload["password"] == PASSWORDS[index]
            return httpx.Response(200, json={"access_token": TOKENS[index], "token_type": "bearer",
                                             "refresh_token": REFRESH, "user": self.user(index)})
        assert request.method == "GET"
        if request.url.path == "/auth/v1/user":
            index = TOKENS.index(request.headers["authorization"].removeprefix("Bearer "))
            return httpx.Response(200, json=self.user(index))
        return await self.database(request)


async def run(fake):
    return await reads.verify(SETTINGS, *CREDENTIALS, transport=httpx.MockTransport(fake))


@pytest.mark.anyio
async def test_real_app_routes_auth_and_repository_reused_without_writes():
    fake = FakeSupabase()
    checks = await run(fake)
    assert len(checks) == 12 and all(check.passed for check in checks)
    assert len([request for request in fake.requests if request.method == "POST"]) == 2
    assert all(request.url.path in {"/auth/v1/token", "/auth/v1/user",
                                   "/rest/v1/diaries", "/rest/v1/diary_messages",
                                   "/rest/v1/diary_assets"}
               for request in fake.requests)
    assert len(fake.database.requests) == 10
    assert all(secret not in repr(checks) for secret in SENSITIVE)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 403, 500])
async def test_unauthenticated_get_requires_exact_401(monkeypatch, status):
    from app import main

    original = main.create_app
    def changed_app(*args, **kwargs):
        app = original(*args, **kwargs)
        @app.middleware("http")
        async def changed_unauthenticated_status(request, call_next):
            if "authorization" not in request.headers:
                return JSONResponse({}, status_code=status)
            return await call_next(request)
        return app
    monkeypatch.setattr(main, "create_app", changed_app)
    checks = await run(FakeSupabase())
    assert all(not check.passed for check in checks[:3])
    assert all(check.passed for check in checks[3:])


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["missing", "wrong_id", "bad_json", "timeout"])
async def test_own_diary_failure_never_passes(mode):
    def override(request):
        if request.url.path == "/rest/v1/diaries" and "id" in request.url.params:
            owner = request.headers["authorization"] == "Bearer fake-member-a"
            own_id = reads.DIARY_A if owner else reads.DIARY_B
            if request.url.params["id"] == f"eq.{own_id}":
                if mode == "missing":
                    return httpx.Response(200, json=[])
                if mode == "wrong_id":
                    row = {**fake.database.diaries[own_id], "id": str(MEMBER_A)}
                    return httpx.Response(200, json=[row])
                if mode == "bad_json":
                    return httpx.Response(200, text=" ".join(SENSITIVE))
                raise httpx.ReadTimeout(" ".join(SENSITIVE))
    fake = FakeSupabase(override)
    checks = await run(fake)
    assert all(not check.passed for check in checks if "본인 일기 상세" in check.name)
    assert sum(not check.passed for check in checks) == 2


@pytest.mark.anyio
async def test_supplied_secret_settings_rejected_before_network():
    fake = FakeSupabase()
    with pytest.raises(ValueError, match="조회 설정 오류"):
        await reads.verify(Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY,
                                    supabase_secret_key="sb_secret_fake_forbidden"),
                           *CREDENTIALS, transport=httpx.MockTransport(fake))
    assert not fake.requests


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["empty", "other", "unknown", "duplicate", "wrong_date"])
async def test_month_requires_known_own_fixture_and_no_extra_rows(mode):
    def override(request):
        if request.url.path == "/rest/v1/diaries" and "diary_date" in request.url.params:
            owner = request.headers["authorization"] == "Bearer fake-member-a"
            own, other = (reads.DIARY_A, reads.DIARY_B) if owner else (reads.DIARY_B, reads.DIARY_A)
            ids = {"empty": [], "other": [own, other], "unknown": [own, str(MEMBER_A)],
                   "duplicate": [own, own], "wrong_date": [own]}[mode]
            return httpx.Response(200, json=[{
                "id": value, "diary_date": "2026-11-08" if mode == "wrong_date" else reads.DIARY_DATE,
                "weather": None, "confirmed_text": TEXT,
            } for value in ids])
    checks = await run(FakeSupabase(override))
    assert sum(not check.passed for check in checks) == 2
    assert all(not check.passed for check in checks if "월별 본인" in check.name)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 401, 403, 500])
async def test_cross_detail_requires_diary_not_found_404(status):
    def override(request):
        if request.url.path == "/rest/v1/diaries" and "id" in request.url.params:
            owner = request.headers["authorization"] == "Bearer fake-member-a"
            own_id = reads.DIARY_A if owner else reads.DIARY_B
            requested = request.url.params["id"].removeprefix("eq.")
            if requested != own_id:
                if status == 200:
                    return httpx.Response(200, json=[fake.database.diaries[requested]])
                return httpx.Response(status, text=" ".join(SENSITIVE))
    fake = FakeSupabase(override)
    checks = await run(fake)
    assert all(not check.passed for check in checks if "상대 일기" in check.name)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["same_id", "anonymous", "wrong_role", "mismatch", "bad_token",
                                  "bad_json", "redirect", "network"])
async def test_invalid_login_identity_prevents_authenticated_db_calls(mode):
    def override(request):
        if request.url.path == "/auth/v1/token":
            if mode == "bad_json":
                return httpx.Response(200, text=" ".join(SENSITIVE))
            if mode == "redirect":
                return httpx.Response(302, headers={"location": "https://other.invalid"})
            if mode == "network":
                raise httpx.ReadTimeout(" ".join(SENSITIVE))
            if mode in {"mismatch", "bad_token"}:
                return httpx.Response(200, json={
                    "access_token": "bad\nheader" if mode == "bad_token" else TOKENS[0],
                    "token_type": "bearer", "user": FakeSupabase.user(1),
                })
        if request.url.path == "/auth/v1/user":
            if mode == "same_id":
                return httpx.Response(200, json=FakeSupabase.user(0))
            if mode in {"anonymous", "wrong_role"}:
                user = {**FakeSupabase.user(0), **({"is_anonymous": True} if mode == "anonymous"
                                                 else {"role": "service_role"})}
                return httpx.Response(200, json=user)
    fake = FakeSupabase(override)
    checks = await run(fake)
    assert all(check.passed for check in checks[:3])
    assert not any(check.passed for check in checks[5:])
    assert not fake.database.requests


@pytest.mark.anyio
@pytest.mark.parametrize("method,path", [
    ("POST", "/rest/v1/diaries"), ("PATCH", "/rest/v1/diaries"),
    ("DELETE", "/rest/v1/diary_messages"), ("POST", "/rest/v1/rpc/confirm_member_diary"),
    ("GET", "/rest/v1/rpc/confirm_member_diary"), ("GET", "/storage/v1/object"),
    ("POST", "/auth/v1/token?grant_type=refresh_token"), ("GET", "/api/chat"),
    ("GET", "https://other.invalid/auth/v1/user"),
    ("GET", "http://nnbmxtiouvsuszretahg.supabase.co/auth/v1/user"),
])
async def test_transport_blocks_writes_rpc_storage_and_other_hosts(method, path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200)
    async with httpx.AsyncClient(base_url=reads.PROJECT_URL, transport=reads.ReadOnlyTransport(
        login=True, inner=httpx.MockTransport(handler),
    )) as http:
        with pytest.raises(RuntimeError, match="허용 외 요청 차단"):
            await http.request(method, path)
    assert not requests


def setup_cli(monkeypatch, fake):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", TEST_PUBLISHABLE_KEY)
    monkeypatch.setenv("SUPABASE_URL", reads.PROJECT_URL)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "invalid-secret-must-be-ignored")
    monkeypatch.setattr(reads.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    values = iter((EMAILS[0], PASSWORDS[0], EMAILS[1], PASSWORDS[1]))
    monkeypatch.setattr(reads.getpass, "getpass", lambda prompt: next(values))
    original = reads.verify
    monkeypatch.setattr(reads, "verify", lambda *args: original(*args, transport=httpx.MockTransport(fake)))


@pytest.mark.parametrize("mode", ["success", "network", "unexpected", "invalid_json"])
def test_cli_prints_only_checks_and_no_sensitive_values(monkeypatch, capsys, caplog, mode):
    def override(request):
        if mode != "success":
            logging.getLogger("httpcore").critical(" ".join(SENSITIVE))
            if mode == "invalid_json":
                return httpx.Response(200, text=" ".join(SENSITIVE))
            error = httpx.ConnectError if mode == "network" else RuntimeError
            raise error(" ".join(SENSITIVE))
    caplog.set_level(logging.DEBUG)
    setup_cli(monkeypatch, FakeSupabase(override))
    assert reads.main([]) == (0 if mode == "success" else 1)
    output = capsys.readouterr()
    combined = output.out + output.err + caplog.text
    assert all(secret not in combined for secret in (*SENSITIVE, "Traceback"))
    assert all(line.endswith((": 성공", ": 실패")) for line in output.out.splitlines())
    assert len(output.out.splitlines()) == 12
    assert reads.os.environ.get("SUPABASE_SECRET_KEY") is None
    assert logging.root.manager.disable == 0


@pytest.mark.parametrize("mode", ["pipe", "echo", "interrupt", "options", "wrong_host", "secret_key"])
def test_cli_unsafe_input_or_config_stops_before_network(monkeypatch, capsys, mode):
    fake = FakeSupabase()
    setup_cli(monkeypatch, fake)
    if mode == "pipe":
        monkeypatch.setattr(reads.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    if mode in {"echo", "interrupt"}:
        def unsafe(prompt):
            if mode == "interrupt":
                raise KeyboardInterrupt
            warnings.warn(" ".join(SENSITIVE), reads.getpass.GetPassWarning)
            pytest.fail("평문 입력 도달")
        monkeypatch.setattr(reads.getpass, "getpass", unsafe)
    if mode == "wrong_host":
        monkeypatch.setenv("SUPABASE_URL", "https://other.invalid")
    if mode == "secret_key":
        monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_secret_fake_forbidden")
    assert reads.main([PASSWORDS[0]] if mode == "options" else []) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "검증 실행: 실패\n"
    assert not fake.requests


def test_credentials_and_member_reprs_hide_sensitive_values():
    assert all(value not in repr(CREDENTIALS) for value in (*EMAILS, *PASSWORDS))
    assert TOKENS[0] not in repr(reads.Member(MEMBER_A, TOKENS[0]))
