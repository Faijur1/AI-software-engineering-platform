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


def test_health_endpoint_reports_the_stateful_dependencies_as_ok(
    client: TestClient,
) -> None:
    """Postgres and Redis are up, so /health must say so.

    Deliberately not asserting an overall 200. /health also checks the model,
    and this tier does not require one -- the first CI run failed here for
    exactly that reason, with a 503 that was the endpoint working correctly
    rather than a fault. Asserting whole-system green in a tier that does not
    bring up the whole system tests the environment, not the code.
    """
    body = client.get("/health").json()

    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["redis"]["status"] == "ok"


def test_a_dependency_being_down_is_reported_rather_than_hidden(
    client: TestClient,
) -> None:
    """Whatever is down, /health names it and refuses to claim to be ok.

    The value of a health check is that a partial outage is visible, so the
    overall verdict must follow the worst dependency rather than the best.
    """
    response = client.get("/health")
    body = response.json()

    down = [n for n, dep in body["dependencies"].items() if dep["status"] != "ok"]
    if down:
        assert body["status"] == "degraded", down
        assert response.status_code == 503
        assert all(body["dependencies"][name].get("error") for name in down)
    else:
        assert body["status"] == "ok"
        assert response.status_code == 200


@pytest.mark.llm
def test_health_is_fully_green_when_the_model_is_reachable(client: TestClient) -> None:
    """The whole-system assertion, in the tier that actually provides a model."""
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert all(dep["status"] == "ok" for dep in body["dependencies"].values())
