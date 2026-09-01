"""Health endpoint.

This performs real connectivity checks against Postgres and Redis. It must never
report ``ok`` without having actually reached each dependency, because its whole
purpose is to be trusted by deployment tooling.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.database import SessionFactory
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.schemas.health import DependencyHealth, HealthResponse

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


def _timed(check: Any) -> DependencyHealth:
    """Run a connectivity probe, reporting latency or a short failure reason."""
    started = time.perf_counter()
    try:
        check()
    except Exception as exc:
        # The message is deliberately truncated: connection errors can embed
        # DSNs, and those may carry credentials.
        logger.warning("health_check_failed", check=getattr(check, "__name__", "?"), error=str(exc))
        return DependencyHealth(status="unavailable", error=type(exc).__name__)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return DependencyHealth(status="ok", latency_ms=round(elapsed_ms, 2))


def _check_database() -> None:
    with SessionFactory() as session:
        session.execute(text("SELECT 1"))


def _check_redis() -> None:
    get_redis().ping()


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    from app.core.config import get_settings

    dependencies = {
        "database": _timed(_check_database),
        "redis": _timed(_check_redis),
    }
    healthy = all(dep.status == "ok" for dep in dependencies.values())
    if not healthy:
        response.status_code = 503

    return HealthResponse(
        status="ok" if healthy else "degraded",
        environment=get_settings().app_env.value,
        dependencies=dependencies,
    )
