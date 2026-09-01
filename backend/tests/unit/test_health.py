"""Health endpoint behaviour.

The point of these tests is that /health reports the *real* state of its
dependencies: it must go degraded when a dependency is down, and it must not
report ok without having actually reached them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ExternalServiceError
from app.routes import health as health_route


def _stub_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every dependency probe succeed.

    Applied even by tests that only care about one dependency: without it a
    test asserting "database is down" would still reach a real Redis or a real
    Ollama, and would fail on a machine where those are not running.
    """
    for probe in ("_check_database", "_check_redis", "_check_ollama"):
        monkeypatch.setattr(health_route, probe, lambda: None)


def test_health_ok_when_dependencies_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_all_ok(monkeypatch)

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

    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(health_route, "_check_database", _boom)

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

    _stub_all_ok(monkeypatch)
    monkeypatch.setattr(health_route, "_check_database", _boom)

    response = client.get("/health")

    assert "hunter2" not in response.text
    assert response.json()["dependencies"]["database"]["error"] == "ConnectionError"


def test_response_carries_trace_id_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_all_ok(monkeypatch)

    response = client.get("/health")

    assert response.headers.get("X-Trace-Id")


def test_inbound_trace_id_is_honoured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_all_ok(monkeypatch)

    response = client.get("/health", headers={"X-Trace-Id": "abc123"})

    assert response.headers["X-Trace-Id"] == "abc123"


def test_health_reports_ollama(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Indexing cannot run without the embedding model, so it is a dependency."""
    _stub_all_ok(monkeypatch)

    body = client.get("/health").json()

    assert body["dependencies"]["ollama"]["status"] == "ok"


def test_health_degrades_when_the_embedding_model_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running Ollama with no model pulled fails every indexing job, so it
    must not be reported as healthy."""
    _stub_all_ok(monkeypatch)

    def _missing() -> None:
        raise ExternalServiceError("Ollama has no model named 'nomic-embed-text'")

    monkeypatch.setattr(health_route, "_check_ollama", _missing)

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["ollama"]["status"] == "unavailable"
    # The exception type only -- messages can carry hostnames and credentials.
    assert body["dependencies"]["ollama"]["error"] == "ExternalServiceError"
    assert body["dependencies"]["database"]["status"] == "ok"
