"""이메일 가입 이후의 BE 회귀. 실제 가입/메일/SDK/PostgreSQL 검증은 아님."""
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeAI, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from tests.test_drawings import DrawingDatabase, DrawingStorage, upload
from tests.test_saved_diaries import FAKE_SECRET, assert_error, prepare

pytestmark = pytest.mark.anyio


def email_user(confirmed_at):
    return {
        "id": str(MEMBER_A), "role": "authenticated", "aud": "authenticated",
        "is_anonymous": False, "email": "new-member@example.test",
        "email_confirmed_at": confirmed_at,
        "app_metadata": {"provider": "email", "providers": ["email"]},
        # 이름/회원 ID metadata는 소유권의 근거가 될 수 없다.
        "user_metadata": {"name": "가상 새 회원", "user_id": str(MEMBER_B)},
    }


@asynccontextmanager
async def email_api(user=None, status=200):
    auth_requests = []

    async def auth(request):
        assert request.method == "GET" and request.url.path == "/auth/v1/user"
        assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
        assert request.headers["authorization"] == "Bearer fake-member-a"
        auth_requests.append(request)
        return httpx.Response(status, json=user)

    db, storage, ai = DrawingDatabase(), DrawingStorage(), FakeAI()
    app = create_app(
        Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY, supabase_secret_key=FAKE_SECRET),
        httpx.MockTransport(ai), httpx.MockTransport(auth),
        httpx.MockTransport(db), httpx.MockTransport(storage),
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://be.test",
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, app, db, storage, ai, auth_requests


@pytest.mark.parametrize("confirmed_at", ["2026-11-10T00:00:00Z", None])
async def test_first_email_member_can_save_drawing_without_profile_registration(confirmed_at):
    # 인증 여부의 정책은 Supabase가 적용한다. 유효한 /user 응답 이후 BE 별도 가입은 없다.
    # 기존 HTTP 저장소 fixture의 합성 회원을 사용하되 DB/Storage는 매번 빈 상태로 시작한다.
    async with email_api(email_user(confirmed_at)) as (client, app, db, storage, ai, auth):
        empty = await client.get("/api/diaries?month=2026-11")
        assert empty.status_code == 200 and empty.json() == {"month": "2026-11", "diaries": []}
        assert not ai.requests and not db.diaries and not storage.objects
        url, body, draft = await prepare(client)
        saved = await client.post(url, json=body)
        assert saved.status_code == 200, saved.text
        diary = saved.json()
        diary_id = diary["diary_id"]
        assert diary["confirmed_text"] == draft["draft"]["text"]
        assert db.diaries[diary_id]["user_id"] == str(MEMBER_A)
        assert (await upload(client, diary_id)).status_code == 200
        detail = await client.get(f"/api/diaries/{diary_id}")
        assert detail.status_code == 200 and detail.json()["imageUrl"]
        assert len(detail.json()["assets"]) == 1 and len(detail.json()["messages"]) == 3
        month = await client.get("/api/diaries", params={"month": diary["diary_date"][:7]})
        assert [item["diary_id"] for item in month.json()["diaries"]] == [diary_id]
        assert len(db.diaries) == len(storage.objects) == 1
        assert all(request.url.path == "/auth/v1/user" for request in auth)


ROUTES = [
    ("POST", "/api/chat-sessions", {}),
    ("POST", "/api/chat-sessions/unknown/messages", {"client_message_id": "id", "text": "가상 이야기"}),
    ("POST", "/api/chat-sessions/unknown/diary-draft", {"client_request_id": "id", "history_revision": 0}),
    ("POST", "/api/chat-sessions/unknown/diaries", {}),
    ("POST", "/api/diaries/11111111-1111-4111-8111-111111111111/drawing", None),
    ("GET", "/api/diaries?month=2026-11", None),
    ("GET", "/api/diaries/11111111-1111-4111-8111-111111111111", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("scenario,status,code", [
    ("no_session", 401, "AUTHENTICATION_FAILED"),
    ("invalid_token", 401, "AUTHENTICATION_FAILED"),
    ("anonymous", 403, "ACCESS_DENIED"),
    ("auth_unavailable", 503, "AUTH_UNAVAILABLE"),
])
async def test_email_auth_failure_blocks_every_diary_route(method, path, body, scenario, status, code):
    user = email_user(None)
    user["is_anonymous"] = scenario == "anonymous"
    auth_status = {"invalid_token": 401, "auth_unavailable": 503}.get(scenario, 200)
    async with email_api(user, auth_status) as (client, app, db, storage, ai, auth):
        if scenario == "no_session":
            # 인증 메일 대기 중 session=null이면 회원가입 응답만으로 API에 접근할 수 없다.
            client.headers.pop("Authorization")
        response = await client.request(method, path, json=body)
        assert_error(response, status, code)
        assert len(auth) == (0 if scenario == "no_session" else 1)
        assert not db.requests and not storage.requests and not ai.requests
        assert not app.state.chat_service.store.sessions and not app.state.chat_service.store.starts


async def test_new_auth_uuid_needs_no_local_member_allowlist():
    user = email_user("2026-11-10T00:00:00Z")
    new_id = uuid4()
    user["id"] = str(new_id)
    async with email_api(user) as (client, app, db, storage, ai, auth):
        response = await client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": "first"})
        assert response.status_code == 201
        session = app.state.chat_service.store.sessions[response.json()["session_id"]]
        assert session.owner_id == new_id
        assert not db.requests and not storage.requests
