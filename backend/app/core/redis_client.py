"""Shared Redis connection pool.

Redis backs the Stage 1 job queue (ADR-003). It is accessed through this module
so that connection settings live in exactly one place.
"""

from __future__ import annotations

from functools import lru_cache

from redis import Redis

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    """Return the process-wide Redis client backed by a connection pool."""
    settings = get_settings()
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        # A dropped connection should fail fast and be retried by the caller
        # rather than hanging a request thread indefinitely.
        health_check_interval=30,
    )
