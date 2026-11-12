"""실제 계정/네트워크 없이 영구 저장 검증의 핵심 안전 경계만 검사."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from scripts import verify_diary_storage as storage
from tests.conftest import FakeAuth, MEMBER_A, TEST_PUBLISHABLE_KEY
from tests.test_saved_diaries import FAKE_SECRET, FakeDatabase

SETTINGS = Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY, supabase_secret_key=FAKE_SECRET)
CREDENTIALS = storage.Credentials("fake-c@example.invalid", "fake-password-c")


class FakeSupabase:
    def __init__(self, mode=None):
        self.mode = mode
        self.database = FakeDatabase()
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/auth/v1/token":
            assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
            assert "authorization" not in request.headers
            assert json.loads(request.content) == {
                "email": CREDENTIALS.email, "password": CREDENTIALS.password,
            }
            if self.mode == "login_failure":
                return httpx.Response(401)
            return httpx.Response(200, json={
                "access_token": "fake-member-a", "token_type": "bearer", "user": {"id": str(MEMBER_A)},
            })
        if path == "/auth/v1/user":
            if self.mode == "anonymous":
                return httpx.Response(200, json={"id": str(MEMBER_A), "role": "authenticated",
                                                "aud": "authenticated", "is_anonymous": True})
            return await FakeAuth()(request)
        if request.method == "GET" and "diary_date" in request.url.params:
            if self.mode == "bad_month":
                return httpx.Response(200, json={"unexpected": True})
            if self.mode == "existing_today":
                return httpx.Response(200, json=[{
                    "id": "44444444-4444-4444-8444-444444444444",
                    "diary_date": storage.seoul_today().isoformat(),
                    "weather": None, "confirmed_text": "private existing text",
                }])
        if (self.mode == "member_ab" and request.method == "GET"
                and request.url.params.get("id") == f"eq.{storage.DIARY_A}"):
            # A/B fixture가 보이는 계정은 본문 응답이 예상 밖이어도 저장하지 않아야 합니다.
            return httpx.Response(200, json=[{"id": storage.DIARY_A}])
        if request.method == "POST":
            if self.mode == "conflict":
                return httpx.Response(409, json={"code": "PT409", "details": "DIARY_DATE_CONFLICT"})
            if self.mode == "invalid_secret":
                return httpx.Response(401)
            if self.mode == "lost_response":
                self.database.lose_response = True
        return await self.database(request)


def rpc_requests(fake):
    return [request for request in fake.requests if "/rpc/" in request.url.path]


@pytest.mark.anyio
async def test_single_save_real_be_flow_and_new_app_read_without_secret():
    fake = FakeSupabase()
    result = await storage.verify(SETTINGS, CREDENTIALS, transport=httpx.MockTransport(fake))
    assert result.passed and result.rpc_attempted
    assert len(rpc_requests(fake)) == len(fake.database.diaries) == 1
    assert result.diary_id in fake.database.diaries
    assert len(fake.database.messages[result.diary_id]) == 3
    assert all(request.url.host == httpx.URL(storage.PROJECT_URL).host for request in fake.requests)
    assert all(request.method == "GET" or request.url.path in {
        "/auth/v1/token", "/rest/v1/rpc/confirm_member_diary",
    } for request in fake.requests)
    second = await storage.verify(SETTINGS, CREDENTIALS, transport=httpx.MockTransport(fake))
    assert not second.passed and not second.rpc_attempted
    assert second.statuses[storage.STAGES[2]] == "실패"
    assert len(rpc_requests(fake)) == len(fake.database.diaries) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["login_failure", "anonymous", "existing_today", "bad_month", "member_ab"])
async def test_preflight_failure_never_sends_rpc(mode):
    fake = FakeSupabase(mode)
    result = await storage.verify(SETTINGS, CREDENTIALS, transport=httpx.MockTransport(fake))
    assert not result.passed and not result.rpc_attempted and not rpc_requests(fake)
    assert not fake.database.diaries


@pytest.mark.anyio
@pytest.mark.parametrize("mode,count", [("conflict", 0), ("invalid_secret", 0), ("lost_response", 1)])
async def test_rpc_failure_never_retries_or_deletes(mode, count):
    fake = FakeSupabase(mode)
    result = await storage.verify(SETTINGS, CREDENTIALS, transport=httpx.MockTransport(fake))
    assert not result.passed and result.rpc_attempted
    assert result.statuses[storage.STAGES[6]] == "실패"
    assert len(rpc_requests(fake)) == 1 and len(fake.database.diaries) == count
    assert all(request.method in {"GET", "POST"} for request in fake.requests)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["unarmed", "twice", "midnight", "other_host", "wrong_owner"])
async def test_transport_blocks_unauthorized_or_repeated_write(monkeypatch, mode):
    sent = []
    def handler(request):
        sent.append(request)
        return httpx.Response(200)
    today = storage.seoul_today()
    guard = storage.SingleWriteTransport(today, inner=httpx.MockTransport(handler))
    guard.owner = None if mode == "unarmed" else MEMBER_A
    if mode == "midnight":
        monkeypatch.setattr(storage, "seoul_today", lambda: today + storage.timedelta(days=1))
    async with httpx.AsyncClient(base_url=storage.PROJECT_URL, transport=guard,
                                 headers={"apikey": FAKE_SECRET}) as client:
        payload = {"p_user_id": str(MEMBER_A) if mode != "wrong_owner" else "other",
                   "p_confirmed_at": storage.datetime.now(storage.timezone.utc).isoformat()}
        path = "/rest/v1/rpc/confirm_member_diary"
        if mode == "twice":
            await client.post(path, json=payload)
        if mode == "other_host":
            path = "https://other.invalid" + path
        with pytest.raises(ValueError):
            await client.post(path, json=payload)
    assert len(sent) == (1 if mode == "twice" else 0)


@pytest.mark.parametrize("argv,secret", [([], FAKE_SECRET), (["--save-member-c"], None)])
def test_cli_requires_explicit_option_and_secret_before_input(monkeypatch, capsys, argv, secret):
    monkeypatch.setattr(storage.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(storage.getpass, "getpass", lambda prompt: pytest.fail("unexpected credential input"))
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", TEST_PUBLISHABLE_KEY)
    if secret:
        monkeypatch.setenv("SUPABASE_SECRET_KEY", secret)
    else:
        monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    assert storage.main(argv) == 1
    assert not any(name in storage.os.environ for name in (
        "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY",
    ))
    output = capsys.readouterr()
    assert FAKE_SECRET not in output.out + output.err


def test_cli_failure_output_hides_sensitive_data(monkeypatch, capsys):
    monkeypatch.setattr(storage.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(storage.Settings, "from_env", lambda: SETTINGS)
    answers = iter((CREDENTIALS.email, CREDENTIALS.password))
    monkeypatch.setattr(storage.getpass, "getpass", lambda prompt: next(answers))
    async def fail(*args, **kwargs):
        raise ValueError(" ".join((FAKE_SECRET, CREDENTIALS.email, CREDENTIALS.password, "fake-member-a")))
    monkeypatch.setattr(storage, "verify", fail)
    assert storage.main(["--save-member-c"]) == 1
    output = capsys.readouterr()
    for value in (FAKE_SECRET, CREDENTIALS.email, CREDENTIALS.password, "fake-member-a"):
        assert value not in output.out + output.err
