import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.anyio


async def test_health_and_openapi():
    app = create_app(Settings())
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["x-request-id"].startswith("req_")
        assert response.headers["cache-control"] == "no-store"
        assert "/health" in (await client.get("/openapi.json")).json()["paths"]


async def test_cors():
    app = create_app(Settings())
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = {"Origin": "http://127.0.0.1:5500",
                   "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "Content-Type,Authorization,Idempotency-Key,X-Request-ID"}
        response = await client.options("/api/chat-sessions", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == headers["Origin"]
        response = await client.get("/health", headers={"Origin": headers["Origin"]})
        assert "X-Request-ID" in response.headers["access-control-expose-headers"]
        headers["Origin"] = "http://untrusted.example"
        assert (await client.options("/api/chat-sessions", headers=headers)).status_code == 400


@pytest.mark.parametrize("values", [
    {"ai_base_url": "ftp://localhost"},
    {"ai_base_url": "http://name:secret@localhost"},
    {"ai_base_url": "http://localhost/api"},
    {"ai_base_url": "http://localhost:bad"},
    {"allowed_origins": ("*",)},
    {"allowed_origins": ("http://localhost:5500/",)},
    {"ai_request_timeout_seconds": 0},
    {"ai_read_timeout_seconds": float("inf")},
])
def test_invalid_settings(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "http://localhost:8002/")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000, http://localhost:5500")
    monkeypatch.setenv("AI_CONNECT_TIMEOUT_SECONDS", "2.5")
    settings = Settings.from_env()
    assert settings.ai_base_url == "http://localhost:8002"
    assert settings.allowed_origins == ("http://localhost:3000", "http://localhost:5500")
    assert settings.ai_connect_timeout_seconds == 2.5
