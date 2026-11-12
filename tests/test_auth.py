import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from conftest import FakeAuth, MEMBER_A
from test_chat_api import assert_error

pytestmark = pytest.mark.anyio

PATHS = [
    ("/api/chat-sessions", {}),
    ("/api/chat-sessions/unknown/messages", {"client_message_id": "same", "text": "가상 이야기"}),
    ("/api/chat-sessions/unknown/diary-draft", {"client_request_id": "same", "history_revision": 0}),
]


@pytest.mark.parametrize("path,body", PATHS)
@pytest.mark.parametrize("authorization", [None, "", "Basic fake", "Bearer", "Bearer fake invalid",
                                           "Bearer fake-invalid", "Bearer fake-expired"])
async def test_invalid_auth_blocks_all_routes_without_state_change(api_factory, path, body, authorization):
    auth = FakeAuth()
    async with api_factory(auth=auth, authenticated=False) as (client, service, ai):
        headers = {"Idempotency-Key": "same"}
        if authorization is not None:
            headers["Authorization"] = authorization
        response = await client.post(path, json=body, headers=headers)
        assert_error(response, 401, "AUTHENTICATION_FAILED")
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["message"] == "인증 실패"
        assert not service.store.sessions and not service.store.starts and not ai.requests
        assert len(auth.requests) == (1 if authorization in {"Bearer fake-invalid", "Bearer fake-expired"} else 0)


async def test_auth_headers_are_not_shared_and_each_request_is_verified(api_factory):
    auth = FakeAuth()
    async with api_factory(auth=auth) as (client, service, ai):
        for name in ("a", "b", "a"):
            response = await client.post("/api/chat-sessions", json={}, headers={
                "Authorization": f"Bearer fake-member-{name}", "Idempotency-Key": name,
            })
            assert response.status_code == 201
        assert [r.headers["authorization"] for r in auth.requests] == [
            "Bearer fake-member-a", "Bearer fake-member-b", "Bearer fake-member-a",
        ]
        assert len(ai.requests) == 2


@pytest.mark.parametrize("changes", [
    {"is_anonymous": True}, {"is_anonymous": None}, {"is_anonymous": "false"},
    {"role": "anon"}, {"role": "service_role"}, {"role": None}, {"aud": "other"},
    {"aud": None}, {"user_metadata": {"role": "authenticated"}, "role": "anon"},
])
async def test_only_nonanonymous_authenticated_members_allowed(api_factory, changes):
    auth = FakeAuth()
    auth.outcomes.append(httpx.Response(200, json={
        "id": str(MEMBER_A), "is_anonymous": False, "role": "authenticated",
        "aud": "authenticated", **changes,
    }))
    async with api_factory(auth=auth) as (client, service, ai):
        response = await client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": "id"})
        assert_error(response, 403, "ACCESS_DENIED")
        assert not ai.requests and not service.store.starts


@pytest.mark.parametrize("outcome,status,code,retryable", [
    (httpx.ConnectError("secret token"), 503, "AUTH_UNAVAILABLE", True),
    (httpx.ReadTimeout("secret token"), 503, "AUTH_UNAVAILABLE", True),
    (httpx.Response(429, text="secret token"), 503, "AUTH_UNAVAILABLE", True),
    (httpx.Response(500, text="secret token"), 503, "AUTH_UNAVAILABLE", True),
    (httpx.Response(503, text="secret token"), 503, "AUTH_UNAVAILABLE", True),
    (httpx.Response(401, text="secret token"), 401, "AUTHENTICATION_FAILED", False),
    (httpx.Response(403, text="secret token"), 401, "AUTHENTICATION_FAILED", False),
    (httpx.Response(400, text="secret token"), 401, "AUTHENTICATION_FAILED", False),
    (httpx.Response(302, headers={"Location": "https://untrusted.test"}), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(404), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(204), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(200, text="secret invalid json"), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(200, json=[]), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(200, json={"id": "fake"}), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(200, json={"id": 123}), 502, "AUTH_INVALID_RESPONSE", False),
    (httpx.Response(200, json={"id": "00000000-0000-0000-0000-000000000000"}), 502, "AUTH_INVALID_RESPONSE", False),
])
async def test_auth_failures_are_sanitized_and_never_retried(api_factory, outcome, status, code, retryable):
    auth = FakeAuth()
    auth.outcomes.append(outcome)
    async with api_factory(auth=auth) as (client, service, ai):
        response = await client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": "id"})
        assert_error(response, status, code, retryable)
        assert len(auth.requests) == 1
        assert not ai.requests and not service.store.starts


async def test_auth_deadline_and_retry_leave_no_reservation(api_factory):
    async def blocked(request):
        await asyncio.Event().wait()

    auth = FakeAuth()
    auth.outcomes.append(blocked)
    async with api_factory(Settings(auth_request_timeout_seconds=0.02), auth=auth) as (client, service, ai):
        headers = {"Idempotency-Key": "same"}
        response = await client.post("/api/chat-sessions", json={}, headers=headers)
        assert_error(response, 503, "AUTH_UNAVAILABLE", True)
        assert not ai.requests and not service.store.starts
        assert (await client.post("/api/chat-sessions", json={}, headers=headers)).status_code == 201
        assert len(auth.requests) == 2 and len(ai.requests) == 1


async def test_health_public_but_missing_auth_config_fails_closed():
    app = create_app(Settings())
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://be.test",
    ) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        response = await client.post("/api/chat-sessions", json={}, headers={
            "Authorization": "Bearer fake-member-a", "Idempotency-Key": "same",
        })
        assert_error(response, 503, "AUTH_NOT_CONFIGURED")
        assert not app.state.chat_service.store.starts
        schema = (await client.get("/openapi.json")).json()
        assert "security" not in schema["paths"]["/health"]["get"]
        for path, _ in PATHS:
            path = path.replace("unknown", "{session_id}")
            assert schema["paths"][path]["post"]["security"] == [{"HTTPBearer": []}]


@pytest.mark.parametrize("values", [
    {"supabase_url": "http://untrusted.test"},
    {"supabase_url": "https://user:secret@untrusted.test"},
    {"supabase_url": "https://untrusted.test/api"},
    {"supabase_url": "https://untrusted.test?key=secret"},
    {"supabase_url": "https://untrusted.test#secret"},
    {"supabase_url": "https://untrusted.test:bad"},
    {"supabase_url": "https://untrusted.test:8443"},
    {"supabase_publishable_key": "sb_secret_fake"},
    {"supabase_publishable_key": "service_role"},
    {"supabase_publishable_key": "eyJ.fake.jwt"},
    {"supabase_publishable_key": ""},
    {"auth_request_timeout_seconds": 0},
    {"auth_request_timeout_seconds": float("inf")},
])
def test_invalid_auth_settings(values):
    with pytest.raises(ValidationError) as error:
        Settings(**values)
    assert "sb_secret_fake" not in str(error.value)


def test_auth_environment_and_key_repr(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://auth.test/")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_fake_example")
    monkeypatch.setenv("AUTH_REQUEST_TIMEOUT_SECONDS", "2.5")
    settings = Settings.from_env()
    assert settings.supabase_url == "https://auth.test"
    assert settings.auth_request_timeout_seconds == 2.5
    assert "sb_publishable_fake_example" not in repr(settings)
