from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router
from app.api.diaries import router as diary_router
from app.clients.ai import AIClient
from app.clients.auth import AuthClient
from app.clients.diaries import DiaryRepository
from app.config import Settings
from app.errors import install_error_handlers
from app.services.chat import ChatService
from app.services.diaries import DiaryService
from app.session_store import SessionStore


def create_app(settings: Settings | None = None,
               ai_transport: httpx.AsyncBaseTransport | None = None,
               auth_transport: httpx.AsyncBaseTransport | None = None,
               db_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        timeout = httpx.Timeout(
            connect=settings.ai_connect_timeout_seconds,
            read=settings.ai_read_timeout_seconds,
            write=settings.ai_write_timeout_seconds,
            pool=settings.ai_pool_timeout_seconds,
        )
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(httpx.AsyncClient(
                base_url=settings.ai_base_url, timeout=timeout,
                transport=ai_transport or httpx.AsyncHTTPTransport(retries=0),
                follow_redirects=False, trust_env=False,
            ))
            app.state.auth_client = None
            reader = None
            if settings.supabase_publishable_key is not None:
                auth_http = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=settings.supabase_url,
                    headers={"apikey": settings.supabase_publishable_key.get_secret_value()},
                    timeout=settings.auth_request_timeout_seconds,
                    transport=auth_transport or httpx.AsyncHTTPTransport(retries=0),
                    follow_redirects=False, trust_env=False,
                ))
                app.state.auth_client = AuthClient(auth_http, settings.auth_request_timeout_seconds)
                reader = auth_http
            writer = None
            if settings.supabase_publishable_key is not None and db_transport is not None:
                reader = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=settings.supabase_url,
                    headers={"apikey": settings.supabase_publishable_key.get_secret_value()},
                    timeout=settings.db_request_timeout_seconds,
                    transport=db_transport,
                    follow_redirects=False, trust_env=False,
                ))
            if settings.supabase_secret_key is not None:
                writer = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=settings.supabase_url,
                    headers={"apikey": settings.supabase_secret_key.get_secret_value()},
                    timeout=settings.db_request_timeout_seconds,
                    transport=db_transport or httpx.AsyncHTTPTransport(retries=0),
                    follow_redirects=False, trust_env=False,
                ))
            store = SessionStore()
            app.state.chat_service = ChatService(
                store, AIClient(http, settings.ai_request_timeout_seconds),
            )
            app.state.diary_service = DiaryService(store, DiaryRepository(
                reader, writer, settings.db_request_timeout_seconds,
            ))
            yield

    app = FastAPI(title="오늘,그리고 BE (잠정 API)", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(diary_router)
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
