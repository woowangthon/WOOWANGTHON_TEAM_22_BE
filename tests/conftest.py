import json
from contextlib import asynccontextmanager

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture
def anyio_backend():
    # httpx가 설치하는 AnyIO pytest 플러그인을 사용하되 asyncio로만 실행합니다.
    return "asyncio"


class FakeAI:
    def __init__(self):
        self.requests = []
        self.outcomes = []

    async def __call__(self, request):
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:8002/api/chat"
        self.requests.append(json.loads(request.content))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if callable(outcome):
                return await outcome(request)
            return outcome
        return httpx.Response(200, json={
            "question": f"가상 질문 {len(self.requests)}", "finished": False,
            "mock": True, "fallback": False,
        })


@pytest.fixture
def api_factory():
    @asynccontextmanager
    async def make(settings=None):
        ai = FakeAI()
        app = create_app(settings or Settings(), httpx.MockTransport(ai))
        # ASGITransport 자체는 lifespan을 실행하지 않으므로 명시적으로 진입합니다.
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://be.test",
        ) as client:
            yield client, app.state.chat_service, ai
    return make
