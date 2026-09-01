"""Error handling contract.

Unexpected exceptions must never leak internal detail to the client, and every
error response must use the same envelope.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError, NotFoundError, register_exception_handlers
from app.core.middleware import TraceMiddleware


def _app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    app.add_middleware(TraceMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_maps_to_its_status_and_code() -> None:
    client = _app_raising(NotFoundError("Repository not found"))

    response = client.get("/boom")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == "Repository not found"


def test_unexpected_exception_is_opaque() -> None:
    client = _app_raising(RuntimeError("secret internal detail: password=hunter2"))

    response = client.get("/boom")

    assert response.status_code == 500
    assert "hunter2" not in response.text
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "An unexpected error occurred"


def test_error_body_includes_trace_id_for_correlation() -> None:
    client = _app_raising(NotFoundError("nope"))

    response = client.get("/boom")

    assert response.json()["error"]["trace_id"]


def test_app_error_subclasses_declare_distinct_statuses() -> None:
    assert AppError.status_code == 500
    assert NotFoundError.status_code == 404
