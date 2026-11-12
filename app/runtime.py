"""Explicit deployment factory; importing app.main never enables a worker."""
from contextlib import asynccontextmanager
import os

from fastapi.responses import JSONResponse

from app.config import Settings
from app.main import create_app


def create_runtime_app():
    flag = os.environ.get("COMIC_WORKER_ENABLED", "0")
    if flag not in {"0", "1"}:
        raise RuntimeError("COMIC_WORKER_ENABLED_must_be_0_or_1")
    enabled = flag == "1"
    settings = Settings.from_env()
    if settings.session_store_mode != "memory":
        raise RuntimeError("runtime_requires_memory_sessions")
    if enabled and not (settings.supabase_publishable_key and settings.supabase_secret_key):
        raise RuntimeError("comic_worker_requires_database_credentials")
    app = create_app(settings, enable_comic_worker=enabled)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            if enabled and not application.state.comic_service.jobs.available:
                raise RuntimeError("comic_worker_startup_probe_failed")
            yield

    app.router.lifespan_context = lifespan

    @app.get("/ready", include_in_schema=False)
    async def ready():
        available = app.state.comic_service.jobs.available
        return JSONResponse({"status": "ok" if not enabled or available else "unavailable",
                             "comic_worker": available},
                            status_code=200 if not enabled or available else 503)

    return app
