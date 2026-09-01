"""Health endpoint behaviour.

The point of these tests is that /health reports the *real* state of its
dependencies: it must go degraded when a dependency is down, and it must not
report ok without having actually reached them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.routes import health as health_route


def test_health_ok_when_dependencies_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_route, "_check_database", lambda: None)
    monkeypatch.setattr(health_route, "_check_redis", lambda: None)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["database"]["status"] == "ok"
    assert body["dependencies"]["redis"]["status"] == "ok"
    assert body["dependencies"]["database"]["latency_ms"] is not None


def test_health_degrades_and_returns_503_when_database_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(health_route, "_check_database", _boom)
    monkeypatch.setattr(health_route, "_check_redis", lambda: None)

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["database"]["status"] == "unavailable"
    assert body["dependencies"]["redis"]["status"] == "ok"


def test_health_error_does_not_leak_connection_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DSN in an exception message must not reach the client."""

    def _boom() -> None:
        raise ConnectionError("could not connect to postgresql://app:hunter2@localhost:5432/aisep")

    monkeypatch.setattr(health_route, "_check_database", _boom)
    monkeypatch.setattr(health_route, "_check_redis", lambda: None)

    response = client.get("/health")

    assert "hunter2" not in response.text
    assert response.json()["dependencies"]["database"]["error"] == "ConnectionError"


def test_response_carries_trace_id_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_route, "_check_database", lambda: None)
    monkeypatch.setattr(health_route, "_check_redis", lambda: None)

    response = client.get("/health")

    assert response.headers.get("X-Trace-Id")


def test_inbound_trace_id_is_honoured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_route, "_check_database", lambda: None)
    monkeypatch.setattr(health_route, "_check_redis", lambda: None)

    response = client.get("/health", headers={"X-Trace-Id": "abc123"})

    assert response.headers["X-Trace-Id"] == "abc123"
