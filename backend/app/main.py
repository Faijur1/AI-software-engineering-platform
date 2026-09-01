"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import TraceMiddleware
from app.routes import auth, health, jobs, repositories

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    app = FastAPI(
        title="AI Software Engineering Platform",
        version="0.1.0",
        # Interactive API docs are a development convenience, not a production
        # surface.
        docs_url=None if settings.is_production else "/docs",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(TraceMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # A single explicit origin, not a wildcard: the session cookie is sent
        # with credentials, and browsers reject "*" in that case anyway.
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(repositories.router)
    app.include_router(jobs.router)

    logger.info("application_started", environment=settings.app_env.value)
    return app


app = create_app()
