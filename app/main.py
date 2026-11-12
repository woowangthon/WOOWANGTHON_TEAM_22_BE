from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router
from app.api.diaries import router as diary_router
from app.api.edits import router as edit_router
from app.api.request_recovery import router as request_recovery_router
from app.clients.request_recovery import RequestRecoveryRepository
from app.services.request_recovery import RequestRecoveryService
from app.clients.edits import EditRepository
from app.services.edits import EditService
from app.api.room import router as room_router
from app.api.comics import router as comic_router
from app.clients.ai import AIClient
from app.clients.auth import AuthClient
from app.clients.diaries import DiaryRepository
from app.clients.drawings import DrawingRepository
from app.clients.sessions import SessionRepository
from app.clients.room import RoomRepository
from app.clients.comics import ComicRepository
from app.clients.comic_ai import ComicAIClient
from app.clients.comic_jobs import ComicJobRepository
from app.services.comic_jobs import ComicJobService
from app.config import Settings
from app.errors import install_error_handlers
from app.services.chat import ChatService
from app.services.diaries import DiaryService
from app.services.drawings import DrawingService
from app.services.room import RoomService
from app.services.comics import ComicService
from app.session_store import SessionStore
from app.persistent_session_store import PersistentSessionStore


def create_app(settings: Settings | None = None,
               ai_transport: httpx.AsyncBaseTransport | None = None,
               auth_transport: httpx.AsyncBaseTransport | None = None,
               db_transport: httpx.AsyncBaseTransport | None = None,
               storage_transport: httpx.AsyncBaseTransport | None = None,
               session_repository: SessionRepository | None = None,
               enable_comic_worker: bool = False) -> FastAPI:
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
            store = (PersistentSessionStore(session_repository or SessionRepository(
                writer, settings.db_request_timeout_seconds, settings.ai_request_timeout_seconds,
            )) if settings.session_store_mode == "supabase" else SessionStore())
            app.state.chat_service = ChatService(
                store, AIClient(http, settings.ai_request_timeout_seconds),
            )
            repository = DiaryRepository(reader, writer, settings.db_request_timeout_seconds)
            storage_clients = [reader, writer]
            if storage_transport is not None:
                storage_clients = []
                for key in (settings.supabase_publishable_key, settings.supabase_secret_key):
                    storage_clients.append(await stack.enter_async_context(httpx.AsyncClient(
                        base_url=settings.supabase_url, headers={"apikey": key.get_secret_value()},
                        timeout=settings.db_request_timeout_seconds, transport=storage_transport,
                        follow_redirects=False, trust_env=False,
                    )) if key is not None else None)
            repository.drawings = DrawingRepository(
                repository, *storage_clients, settings.db_request_timeout_seconds,
            )
            app.state.room_service = RoomService(
                RoomRepository(writer, settings.db_request_timeout_seconds),
            )
            app.state.diary_service = DiaryService(store, repository, app.state.room_service)
            app.state.drawing_service = DrawingService(repository.drawings)
            app.state.edit_service = EditService(EditRepository(repository), repository.drawings)
            app.state.request_recovery_service = RequestRecoveryService(
                RequestRecoveryRepository(writer, settings.db_request_timeout_seconds), repository,
            )
            app.state.comic_service = ComicService(
                store, ComicRepository(repository, repository.drawings),
                ComicAIClient(http, settings.ai_request_timeout_seconds),
            )
            # 기본 app은 비활성. 명시적 활성화 및 시작 검사는 app.runtime factory에서 수행한다.
            comic_http = await stack.enter_async_context(httpx.AsyncClient(
                base_url=settings.ai_base_url,
                timeout=httpx.Timeout(connect=3, read=200, write=10, pool=3),
                transport=ai_transport or httpx.AsyncHTTPTransport(retries=0),
                follow_redirects=False, trust_env=False,
            ))
            jobs = ComicJobService(app.state.comic_service,
                                   ComicJobRepository(writer, settings.db_request_timeout_seconds),
                                   ComicAIClient(comic_http, 210))
            app.state.comic_service.jobs = jobs
            if enable_comic_worker:
                await jobs.start()
            try:
                yield
            finally:
                await jobs.stop()

    app = FastAPI(title="오늘,그리고 BE (잠정 API)", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(diary_router)
    app.include_router(edit_router)
    app.include_router(request_recovery_router)
    app.include_router(room_router)
    app.include_router(comic_router)
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "If-Match", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
