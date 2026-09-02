"""The per-repository cloud opt-in, end to end.

The unit tests pin the resolution rule. These pin the things only a real
database and a real request can show: that a newly connected repository is
denied, that the grant is owner-scoped, and that withdrawal actually persists.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.database import session_scope
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.integration

ALICE = 900_000_701
BOB = 900_000_702

REPO = {
    "id": 900_000_710,
    "name": "sample",
    "full_name": "alice/sample",
    "owner": {"login": "alice"},
    "description": None,
    "default_branch": "main",
    "private": True,
    "language": "Python",
    "pushed_at": "2026-08-30T10:00:00Z",
    "html_url": "https://github.com/alice/sample",
}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def purge() -> None:
        with session_scope() as session:
            session.execute(delete(User).where(User.github_id.in_([ALICE, BOB])))

    purge()
    yield
    purge()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Callable[[int, str], None]:
    original = httpx.Client
    active: list[Any] = []

    def factory(**kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return original(transport=httpx.MockTransport(lambda r: active[-1](r)))

    monkeypatch.setattr(httpx, "Client", factory)

    def install(profile_id: int, login: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/login/oauth/access_token":
                return httpx.Response(200, json={"access_token": f"gho_{login}"})
            if path == "/user":
                return httpx.Response(
                    200, json={"id": profile_id, "login": login, "email": None}
                )
            if path.startswith("/repos/"):
                return httpx.Response(200, json=REPO)
            return httpx.Response(404, json={"message": "Not Found"})

        active.append(handler)

    return install


def _sign_in(client: TestClient) -> None:
    from app.routes.auth import _STATE_COOKIE

    state = client.get("/auth/github/login", follow_redirects=False).cookies[_STATE_COOKIE]
    assert (
        client.get(
            f"/auth/github/callback?code=c&state={state}", follow_redirects=False
        ).status_code
        == 303
    )


def _connect(client: TestClient) -> str:
    created = client.post("/repositories", json={"owner": "alice", "name": "sample"})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


# --- the default ------------------------------------------------------------


def test_a_newly_connected_repository_is_denied_by_default(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    """Nothing about connecting a repository implies consent to send it away."""
    github(ALICE, "alice")
    _sign_in(client)

    body = client.post("/repositories", json={"owner": "alice", "name": "sample"}).json()

    assert body["allow_cloud_llm"] is False
    assert body["cloud_llm_allowed_at"] is None


# --- granting and withdrawing ----------------------------------------------


def test_granting_records_when_permission_was_given(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)

    response = client.patch(
        f"/repositories/{repository_id}/settings", json={"allow_cloud_llm": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allow_cloud_llm"] is True
    # Consent is auditable, not merely current.
    assert body["cloud_llm_allowed_at"] is not None


def test_withdrawing_clears_the_grant_and_persists(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)
    client.patch(f"/repositories/{repository_id}/settings", json={"allow_cloud_llm": True})

    withdrawn = client.patch(
        f"/repositories/{repository_id}/settings", json={"allow_cloud_llm": False}
    )

    assert withdrawn.json()["allow_cloud_llm"] is False
    assert withdrawn.json()["cloud_llm_allowed_at"] is None
    with session_scope() as session:
        row = session.get(Repository, uuid.UUID(repository_id))
        assert row is not None
        assert row.allow_cloud_llm is False


def test_regranting_does_not_rewrite_the_original_timestamp_on_a_no_op(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    """The timestamp answers "when was this granted", not "last confirmed"."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)
    first = client.patch(
        f"/repositories/{repository_id}/settings", json={"allow_cloud_llm": True}
    ).json()["cloud_llm_allowed_at"]

    again = client.patch(
        f"/repositories/{repository_id}/settings", json={"allow_cloud_llm": True}
    ).json()["cloud_llm_allowed_at"]

    assert again == first


# --- who may change it ------------------------------------------------------


def test_changing_settings_requires_a_session(anonymous_client: TestClient) -> None:
    response = anonymous_client.patch(
        f"/repositories/{uuid.uuid4()}/settings", json={"allow_cloud_llm": True}
    )

    assert response.status_code == 401


def test_another_users_repository_is_reported_as_absent(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    """404 rather than 403: existence is not disclosed to a non-owner."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)
    client.post("/auth/logout")

    github(BOB, "bob")
    _sign_in(client)
    response = client.patch(
        f"/repositories/{repository_id}/settings", json={"allow_cloud_llm": True}
    )

    assert response.status_code == 404
    with session_scope() as session:
        row = session.get(Repository, uuid.UUID(repository_id))
        assert row is not None
        assert row.allow_cloud_llm is False, "a non-owner must not be able to grant"


def test_the_permission_is_not_settable_through_the_connect_endpoint(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    """Consent has one route in, so there is one place to audit it."""
    github(ALICE, "alice")
    _sign_in(client)

    body = client.post(
        "/repositories",
        json={"owner": "alice", "name": "sample", "allow_cloud_llm": True},
    ).json()

    assert body["allow_cloud_llm"] is False
