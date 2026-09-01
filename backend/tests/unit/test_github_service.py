"""GitHub client behaviour, with the network replaced by a mock transport.

The interesting cases are GitHub's non-obvious ones: it reports a failed token
exchange with HTTP 200, and its repository payloads have optional fields that
are absent for real repositories.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.core.errors import AuthenticationError, ExternalServiceError, NotFoundError
from app.services import github

# A handler standing in for github.com, and the fixture that installs one.
Handler = Callable[[httpx.Request], httpx.Response]
InstallMock = Callable[[Handler], list[httpx.Request]]


@pytest.fixture
def mock_github(monkeypatch: pytest.MonkeyPatch) -> InstallMock:
    """Replace httpx.Client with one backed by a caller-supplied handler.

    Returns the list of recorded requests so tests can assert on what was sent,
    including that a token was passed as a header rather than a query parameter.
    """

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        original = httpx.Client

        def factory(**kwargs: object) -> httpx.Client:
            kwargs.pop("timeout", None)
            kwargs.pop("follow_redirects", None)
            return original(transport=httpx.MockTransport(recording))

        monkeypatch.setattr(httpx, "Client", factory)
        return seen

    return install


def _json(payload: object, status_code: int = 200) -> Handler:
    return lambda _request: httpx.Response(status_code, json=payload)


REPO_PAYLOAD = {
    "id": 4567,
    "name": "platform",
    "full_name": "octocat/platform",
    "owner": {"login": "octocat"},
    "description": "A repository",
    "default_branch": "develop",
    "private": True,
    "language": "Python",
    "pushed_at": "2026-08-30T10:00:00Z",
    "html_url": "https://github.com/octocat/platform",
}


def test_exchange_code_returns_the_access_token(mock_github: InstallMock) -> None:
    requests = mock_github(_json({"access_token": "gho_abc", "token_type": "bearer"}))

    assert github.exchange_code_for_token("the-code") == "gho_abc"
    # The secret belongs in the POST body, never in a URL that could be logged.
    assert "client_secret" not in str(requests[0].url)


def test_exchange_code_failure_reported_as_http_200_is_still_a_failure(
    mock_github: InstallMock,
) -> None:
    """GitHub returns 200 with an ``error`` field for a bad or reused code."""
    mock_github(
        _json({"error": "bad_verification_code", "error_description": "The code is incorrect."})
    )

    with pytest.raises(AuthenticationError):
        github.exchange_code_for_token("a-stale-code")


def test_exchange_code_without_a_token_is_an_upstream_error(mock_github: InstallMock) -> None:
    mock_github(_json({"token_type": "bearer"}))

    with pytest.raises(ExternalServiceError):
        github.exchange_code_for_token("the-code")


def test_expired_token_surfaces_as_unauthenticated(mock_github: InstallMock) -> None:
    mock_github(_json({"message": "Bad credentials"}, status_code=401))

    with pytest.raises(AuthenticationError):
        github.list_repositories("gho_revoked")


def test_invisible_repository_surfaces_as_not_found(mock_github: InstallMock) -> None:
    """GitHub 404s a repository the token cannot see; so do we (docs/api.md).

    Not a 502: GitHub answered correctly. Not a 403: that would confirm the
    repository exists.
    """
    mock_github(_json({"message": "Not Found"}, status_code=404))

    with pytest.raises(NotFoundError):
        github.get_repository("gho_abc", "someone", "private")


def test_github_outage_surfaces_as_an_upstream_error(mock_github: InstallMock) -> None:
    mock_github(_json({"message": "Server Error"}, status_code=503))

    with pytest.raises(ExternalServiceError):
        github.list_repositories("gho_abc")


def test_network_failure_surfaces_as_an_upstream_error(mock_github: InstallMock) -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed")

    mock_github(boom)

    with pytest.raises(ExternalServiceError):
        github.list_repositories("gho_abc")


def test_token_is_sent_as_a_header_not_a_query_parameter(mock_github: InstallMock) -> None:
    requests = mock_github(_json([REPO_PAYLOAD]))

    github.list_repositories("gho_secret")

    assert requests[0].headers["Authorization"] == "Bearer gho_secret"
    assert "gho_secret" not in str(requests[0].url)


def test_repository_payload_is_mapped(mock_github: InstallMock) -> None:
    mock_github(_json([REPO_PAYLOAD]))

    repo = github.list_repositories("gho_abc")[0]

    assert repo.id == 4567
    assert repo.owner == "octocat"
    assert repo.name == "platform"
    assert repo.full_name == "octocat/platform"
    assert repo.default_branch == "develop"
    assert repo.is_private is True
    assert repo.updated_at == "2026-08-30T10:00:00Z"


def test_empty_repository_gets_a_default_branch(mock_github: InstallMock) -> None:
    """A repository with no commits reports ``default_branch: null``."""
    payload = {**REPO_PAYLOAD, "default_branch": None, "language": None, "description": None}
    mock_github(_json([payload]))

    repo = github.list_repositories("gho_abc")[0]

    assert repo.default_branch == "main"
    assert repo.language is None


def test_per_page_is_capped_at_the_github_maximum(mock_github: InstallMock) -> None:
    requests = mock_github(_json([]))

    github.list_repositories("gho_abc", per_page=500)

    assert requests[0].url.params["per_page"] == str(github.MAX_PER_PAGE)


def test_profile_falls_back_to_the_verified_primary_email(mock_github: InstallMock) -> None:
    """A user with a private email gets ``null`` from /user."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(
                200, json={"id": 1, "login": "octocat", "name": "Octo", "email": None}
            )
        return httpx.Response(
            200,
            json=[
                {"email": "old@example.com", "primary": False, "verified": True},
                {"email": "octo@example.com", "primary": True, "verified": True},
            ],
        )

    mock_github(handler)

    assert github.get_authenticated_user("gho_abc").email == "octo@example.com"


def test_unverified_email_is_not_used(mock_github: InstallMock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 1, "login": "octocat", "email": None})
        return httpx.Response(
            200, json=[{"email": "unverified@example.com", "primary": True, "verified": False}]
        )

    mock_github(handler)

    assert github.get_authenticated_user("gho_abc").email is None


def test_authorize_url_contains_the_state(mock_github: InstallMock) -> None:
    url = github.authorize_url("the-state")

    assert url.startswith(github.AUTHORIZE_URL)
    assert "state=the-state" in url
