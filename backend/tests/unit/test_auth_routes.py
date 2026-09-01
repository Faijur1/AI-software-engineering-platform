"""Authentication endpoints: redirect, CSRF state, and unauthenticated access.

The tests that matter here are the negative ones. A sign-in flow that works is
easy; one that refuses a forged callback is the actual security property.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.routes.auth import _STATE_COOKIE


def test_login_redirects_to_github_with_state(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 307
    location = urlparse(response.headers["location"])
    assert location.netloc == "github.com"

    query = parse_qs(location.query)
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/github/callback"]
    # The state in the URL must be the one pinned in the cookie.
    assert query["state"] == [response.cookies[_STATE_COOKIE]]


def test_login_requests_only_documented_scopes(anonymous_client: TestClient) -> None:
    """Stage 1 performs no GitHub writes, so no write scope may be requested."""
    response = anonymous_client.get("/auth/github/login", follow_redirects=False)
    scopes = parse_qs(urlparse(response.headers["location"]).query)["scope"][0].split()

    assert set(scopes) == {"read:user", "user:email", "repo"}
    assert not any(scope.startswith(("write:", "admin:", "delete_")) for scope in scopes)


def test_state_cookie_is_httponly(anonymous_client: TestClient) -> None:
    """Script-readable state would defeat the point of pinning it."""
    response = anonymous_client.get("/auth/github/login", follow_redirects=False)
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_without_credentials_configured_fails_loudly(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    get_settings.cache_clear()

    response = anonymous_client.get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_callback_without_state_cookie_is_rejected(anonymous_client: TestClient) -> None:
    """A callback the application never initiated must not sign anyone in."""
    response = anonymous_client.get(
        "/auth/github/callback?code=abc&state=attacker-chosen", follow_redirects=False
    )

    assert response.status_code == 303
    assert "auth_error=invalid_state" in response.headers["location"]
    assert get_settings().session_cookie_name not in response.cookies


def test_callback_with_mismatched_state_is_rejected(anonymous_client: TestClient) -> None:
    anonymous_client.cookies.set(_STATE_COOKIE, "the-real-state")

    response = anonymous_client.get(
        "/auth/github/callback?code=abc&state=a-different-state", follow_redirects=False
    )

    assert response.status_code == 303
    assert "auth_error=invalid_state" in response.headers["location"]
    assert get_settings().session_cookie_name not in response.cookies


def test_callback_when_user_declines_redirects_with_a_reason(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get(
        "/auth/github/callback?error=access_denied", follow_redirects=False
    )

    assert response.status_code == 303
    assert "auth_error=access_denied" in response.headers["location"]


def test_callback_redirects_to_the_configured_frontend(anonymous_client: TestClient) -> None:
    """The redirect target comes from configuration, never from the request."""
    response = anonymous_client.get(
        "/auth/github/callback?error=access_denied", follow_redirects=False
    )

    assert response.headers["location"].startswith(get_settings().frontend_url)


def test_me_requires_a_session(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_me_rejects_a_forged_cookie(anonymous_client: TestClient) -> None:
    anonymous_client.cookies.set(get_settings().session_cookie_name, "not.a.jwt")

    response = anonymous_client.get("/auth/me")

    assert response.status_code == 401


def test_repositories_require_a_session(anonymous_client: TestClient) -> None:
    for method, path in (
        ("GET", "/repositories"),
        ("GET", "/repositories/github"),
        ("POST", "/repositories"),
    ):
        response = anonymous_client.request(method, path, json={"owner": "a", "name": "b"})
        assert response.status_code == 401, path


def test_logout_clears_the_session_without_requiring_one(
    anonymous_client: TestClient,
) -> None:
    """Signing out with an already-invalid session should succeed quietly."""
    response = anonymous_client.post("/auth/logout")

    assert response.status_code == 204
    assert 'aisep_session=""' in response.headers["set-cookie"]
