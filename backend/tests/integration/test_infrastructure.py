"""Integration checks against the real Postgres and Redis from docker-compose.

Run with: docker compose up -d && pytest -m integration
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.database import SessionFactory, engine
from app.core.redis_client import get_redis

pytestmark = pytest.mark.integration


def test_pgvector_extension_is_installed() -> None:
    with SessionFactory() as session:
        result = session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
    assert result == 1, "pgvector extension missing — run 'alembic upgrade head'"


def test_migrations_created_core_tables() -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"users", "repositories", "alembic_version"} <= tables


def test_redis_is_reachable() -> None:
    assert get_redis().ping() is True


def test_health_endpoint_reports_ok_against_real_dependencies(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["redis"]["status"] == "ok"
