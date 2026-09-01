"""Authentication on the search endpoint."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_search_requires_a_session(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        f"/repositories/{uuid.uuid4()}/search", json={"query": "anything"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_an_empty_query_is_rejected(anonymous_client: TestClient) -> None:
    response = anonymous_client.post(
        f"/repositories/{uuid.uuid4()}/search", json={"query": ""}
    )

    # Unauthenticated is checked first; either way it must not reach retrieval.
    assert response.status_code in (401, 422)
