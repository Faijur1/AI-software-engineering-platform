"""End-to-end sign-in and repository connection against a real database.

GitHub is mocked — the point is not to test GitHub, it is to test that a
completed OAuth round trip produces a real user row, a working session, and
repository records that one user cannot reach from another user's session.

Needs Postgres running and migrated: ``docker compose up -d && alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import session_scope
from app.core.security import decrypt_token
from app.models.repository import Repository
from app.models.user import User
from app.routes.auth import _STATE_COOKIE

pytestmark = pytest.mark.integration

Handler = Callable[[httpx.Request], httpx.Response]

# GitHub IDs are namespaced well above anything real so a failed cleanup cannot
# collide with another test's data.
ALICE_GITHUB_ID = 900_000_001
BOB_GITHUB_ID = 900_000_002

REPO = {
    "id": 900_000_100,
    "name": "platform",
    "full_name": "alice/platform",
    "owner": {"login": "alice"},
    "description": "A test repository",
    "default_branch": "main",
    "private": False,
    "language": "Python",
    "pushed_at": "2026-08-30T10:00:00Z",
    "html_url": "https://github.com/alice/platform",
}


@pytest.fixture(autouse=True)
def _clean_users() -> Iterator[None]:
    """Remove the test users before and after, so runs are independent.

    Repositories cascade from the user, so deleting the user is sufficient.
    """

    def purge() -> None:
        with session_scope() as session:
            session.execute(
                delete(User).where(User.github_id.in_([ALICE_GITHUB_ID, BOB_GITHUB_ID]))
            )

    purge()
    yield
    purge()


def _github(profile_id: int, login: str, token: str = "gho_alice") -> Handler:
    """A stand-in GitHub that accepts one code and serves one repository."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
        if path == "/user":
            return httpx.Response(
                200,
                json={
                    "id": profile_id,
                    "login": login,
                    "name": login.title(),
                    "email": f"{login}@example.com",
                    "avatar_url": f"https://avatars.githubusercontent.com/{login}",
                },
            )
        if path == "/user/repos":
            return httpx.Response(200, json=[REPO])
        if path.startswith("/repos/"):
            return httpx.Response(200, json=REPO)
        return httpx.Response(404, json={"message": "Not Found"})

    return handler


@pytest.fixture
def install_github(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], None]:
    """Point the GitHub client at a mock, replaceable mid-test.

    The real ``httpx.Client`` is captured once and the active handler is held in
    a mutable cell, so calling ``install`` a second time swaps the handler
    rather than wrapping the previous mock in another layer.
    """
    original = httpx.Client
    active: list[Handler] = []

    def factory(**kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return original(transport=httpx.MockTransport(lambda request: active[-1](request)))

    monkeypatch.setattr(httpx, "Client", factory)

    def install(handler: Handler) -> None:
        active.append(handler)

    return install


def _sign_in(client: TestClient) -> None:
    """Drive a full OAuth round trip, leaving the client holding a session."""
    login = client.get("/auth/github/login", follow_redirects=False)
    state = login.cookies[_STATE_COOKIE]

    callback = client.get(
        f"/auth/github/callback?code=the-code&state={state}", follow_redirects=False
    )
    assert callback.status_code == 303, callback.text


def test_successful_sign_in_creates_a_user_and_a_session(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice"))

    _sign_in(client)

    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["github_id"] == ALICE_GITHUB_ID
    assert body["login"] == "alice"
    assert body["email"] == "alice@example.com"


def test_the_access_token_is_never_returned_to_the_client(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice", token="gho_supersecret"))
    _sign_in(client)

    assert "gho_supersecret" not in client.get("/auth/me").text
    # ...nor in the cookie the browser holds.
    assert "gho_supersecret" not in str(client.cookies)


def test_the_access_token_is_encrypted_at_rest(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice", token="gho_supersecret"))
    _sign_in(client)

    with session_scope() as session:
        user = session.execute(
            select(User).where(User.github_id == ALICE_GITHUB_ID)
        ).scalar_one()
        stored = user.github_token_encrypted

    assert stored is not None
    assert "gho_supersecret" not in stored
    assert decrypt_token(stored) == "gho_supersecret"


def test_signing_in_again_updates_rather_than_duplicates_the_user(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    """A renamed GitHub account stays the same user, matched on GitHub ID."""
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)

    install_github(_github(ALICE_GITHUB_ID, "alice-renamed"))
    _sign_in(client)

    with session_scope() as session:
        users = list(
            session.execute(select(User).where(User.github_id == ALICE_GITHUB_ID)).scalars()
        )

    assert len(users) == 1
    assert users[0].login == "alice-renamed"


def test_connecting_and_listing_repositories(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)

    assert client.get("/repositories").json() == []

    created = client.post("/repositories", json={"owner": "alice", "name": "platform"})
    assert created.status_code == 201
    assert created.json()["index_status"] == "not_indexed"

    listed = client.get("/repositories").json()
    assert [r["name"] for r in listed] == ["platform"]

    # The GitHub listing now marks that repository as already connected.
    from_github = client.get("/repositories/github").json()
    assert from_github["items"][0]["connected_id"] == created.json()["id"]
    assert from_github["has_next"] is False


def test_connecting_twice_is_idempotent(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)

    first = client.post("/repositories", json={"owner": "alice", "name": "platform"})
    second = client.post("/repositories", json={"owner": "alice", "name": "platform"})

    assert second.json()["id"] == first.json()["id"]
    assert len(client.get("/repositories").json()) == 1


def test_connecting_a_repository_github_hides_returns_not_found(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    """Authorisation is GitHub's answer to the caller's own token, not the body."""
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)

    def hidden(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/"):
            return httpx.Response(404, json={"message": "Not Found"})
        return _github(ALICE_GITHUB_ID, "alice")(request)

    install_github(hidden)
    response = client.post("/repositories", json={"owner": "someone", "name": "private"})

    # 404, matching the contract in docs/api.md: a resource not visible to the
    # caller is reported as absent. Not 502 — GitHub answered correctly, and
    # not 403, which would confirm the repository exists.
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert client.get("/repositories").json() == []


def test_one_user_cannot_see_or_delete_another_users_repository(
    client: TestClient,
    install_github: Callable[[Handler], None],
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)
    alice_repo_id = client.post(
        "/repositories", json={"owner": "alice", "name": "platform"}
    ).json()["id"]

    # A second client is a second browser: a fresh cookie jar, a second account.
    with TestClient(client.app, raise_server_exceptions=False) as bob:
        install_github(_github(BOB_GITHUB_ID, "bob", token="gho_bob"))
        _sign_in(bob)

        assert bob.get("/repositories").json() == []
        # 404, not 403: the existence of Alice's repository is not disclosed.
        assert bob.delete(f"/repositories/{alice_repo_id}").status_code == 404

    assert len(client.get("/repositories").json()) == 1


def test_disconnecting_a_repository(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)
    repo_id = client.post("/repositories", json={"owner": "alice", "name": "platform"}).json()[
        "id"
    ]

    assert client.delete(f"/repositories/{repo_id}").status_code == 204
    assert client.get("/repositories").json() == []

    with session_scope() as session:
        assert session.get(Repository, repo_id) is None


def test_logout_ends_the_session(
    client: TestClient, install_github: Callable[[Handler], None]
) -> None:
    install_github(_github(ALICE_GITHUB_ID, "alice"))
    _sign_in(client)
    assert client.get("/auth/me").status_code == 200

    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
