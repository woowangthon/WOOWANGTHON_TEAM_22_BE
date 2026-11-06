from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router
from app.clients.ai import AIClient
from app.config import Settings
from app.errors import install_error_handlers
from app.services.chat import ChatService
from app.session_store import SessionStore


def create_app(settings: Settings | None = None,
               ai_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        timeout = httpx.Timeout(
            connect=settings.ai_connect_timeout_seconds,
            read=settings.ai_read_timeout_seconds,
            write=settings.ai_write_timeout_seconds,
            pool=settings.ai_pool_timeout_seconds,
        )
        async with httpx.AsyncClient(
            base_url=settings.ai_base_url, timeout=timeout,
            transport=ai_transport or httpx.AsyncHTTPTransport(retries=0),
            follow_redirects=False, trust_env=False,
        ) as http:
            app.state.chat_service = ChatService(
                SessionStore(), AIClient(http, settings.ai_request_timeout_seconds),
            )
            yield

    app = FastAPI(title="오늘,그리고 BE (잠정 API)", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
