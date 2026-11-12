import json
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.main import create_app

MEMBER_A = UUID("11111111-1111-4111-8111-111111111111")
MEMBER_B = UUID("22222222-2222-4222-8222-222222222222")
TEST_PUBLISHABLE_KEY = "sb_publishable_fake_test_key"


class FakeAuth:
    def __init__(self):
        self.requests = []
        self.outcomes = []

    async def __call__(self, request):
        assert request.method == "GET"
        assert str(request.url) == "https://nnbmxtiouvsuszretahg.supabase.co/auth/v1/user"
        assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
        assert not request.headers.get("cookie")
        self.requests.append(request)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if callable(outcome):
                return await outcome(request)
            return outcome
        member_id = {"Bearer fake-member-a": MEMBER_A,
                     "Bearer fake-member-b": MEMBER_B}.get(request.headers.get("authorization"))
        if member_id is None:
            return httpx.Response(401, json={"message": "fake invalid or expired token"})
        return httpx.Response(200, json={
            "id": str(member_id), "role": "authenticated", "aud": "authenticated",
            "is_anonymous": False,
        })


@pytest.fixture
def anyio_backend():
    # httpx가 설치하는 AnyIO pytest 플러그인을 사용하되 asyncio로만 실행합니다.
    return "asyncio"


class FakeAI:
    def __init__(self):
        self.requests = []
        self.paths = []
        self.outcomes = []

    async def __call__(self, request):
        assert request.method == "POST"
        assert request.url.path in {"/api/chat", "/api/diary"}
        assert str(request.url).startswith("http://127.0.0.1:8002/api/")
        self.requests.append(json.loads(request.content))
        self.paths.append(request.url.path)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if callable(outcome):
                return await outcome(request)
            return outcome
        if request.url.path == "/api/diary":
            sources = [{"id": index, "text": item["text"]}
                       for index, item in enumerate(self.requests[-1]["messages"])
                       if item["role"] == "child"]
            text = "\n".join(source["text"] for source in sources)
            return httpx.Response(200, json={
                "text": text, "sources": sources,
                "pages": [{"text": text, "source_ids": [source["id"] for source in sources],
                           "imageUrl": None}],
                "fallback": True, "requires_confirmation": True, "mock": True,
            })
        return httpx.Response(200, json={
            "question": f"가상 질문 {len(self.requests)}", "finished": False,
            "mock": True, "fallback": False,
        })


@pytest.fixture
def api_factory():
    @asynccontextmanager
    async def make(settings=None, auth=None, authenticated=True):
        ai = FakeAI()
        auth = auth or FakeAuth()
        settings = (settings or Settings()).model_copy(update={
            "supabase_publishable_key": Settings(
                supabase_publishable_key=TEST_PUBLISHABLE_KEY,
            ).supabase_publishable_key,
        })
        app = create_app(settings, httpx.MockTransport(ai), httpx.MockTransport(auth))
        # ASGITransport 자체는 lifespan을 실행하지 않으므로 명시적으로 진입합니다.
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://be.test",
            headers={"Authorization": "Bearer fake-member-a"} if authenticated else {},
        ) as client:
            yield client, app.state.chat_service, ai
    return make
