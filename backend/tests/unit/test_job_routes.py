"""Authentication and authorisation on the ingestion endpoints."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_indexing_endpoints_require_a_session(anonymous_client: TestClient) -> None:
    repository_id = uuid.uuid4()

    for method, path in (
        ("POST", f"/repositories/{repository_id}/index"),
        ("GET", f"/jobs/{uuid.uuid4()}"),
    ):
        response = anonymous_client.request(method, path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthenticated"


def test_malformed_ids_are_rejected_before_any_lookup(
    anonymous_client: TestClient,
) -> None:
    """A non-UUID path parameter is a validation error, not a 500."""
    response = anonymous_client.get("/jobs/not-a-uuid")
    assert response.status_code in (401, 422)
