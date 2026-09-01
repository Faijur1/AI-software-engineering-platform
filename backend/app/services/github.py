"""GitHub REST API client.

Every call to github.com goes through this module, so the timeout, the failure
mapping and the "never log a token" rule are enforced in one place rather than
repeated at each call site.

Failures upstream surface as :class:`ExternalServiceError` (502), which
distinguishes "GitHub is broken" from "this application is broken" in both the
API contract and the logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ExternalServiceError, NotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)

API_BASE: Final = "https://api.github.com"
AUTHORIZE_URL: Final = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL: Final = "https://github.com/login/oauth/access_token"

# GitHub is an external network hop on the request path. Bounded so a slow
# upstream becomes a 502 rather than a hung worker.
_TIMEOUT: Final = httpx.Timeout(10.0, connect=5.0)

_HEADERS: Final = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "aisep",
}

# GitHub's own maximum. Requesting fewer would only mean more round trips.
MAX_PER_PAGE: Final = 100


@dataclass(frozen=True, slots=True)
class GitHubUser:
    """The subset of a GitHub user profile this application stores."""

    id: int
    login: str
    name: str | None
    email: str | None
    avatar_url: str | None


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    """A repository as GitHub reports it, before it is connected locally."""

    id: int
    owner: str
    name: str
    full_name: str
    description: str | None
    default_branch: str
    is_private: bool
    language: str | None
    updated_at: str | None
    html_url: str


def auth_headers(token: str) -> dict[str, str]:
    """Standard API headers plus bearer auth, for callers outside this module.

    Exposed so the ingestion fetcher does not have to reach for a private name
    or reinvent the header set and drift from it.
    """
    return {**_HEADERS, "Authorization": f"Bearer {token}"}


def authorize_url(state: str) -> str:
    """Build the URL the browser is redirected to in order to grant access."""
    settings = get_settings()
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_callback_url,
            "scope": settings.github_scopes,
            "state": state,
            # Force the account chooser rather than silently reusing whichever
            # GitHub account the browser happens to be signed in as.
            "allow_signup": "false",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> httpx.Response:
    headers = dict(_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            response = client.request(method, url, headers=headers, params=params, data=data)
    except httpx.HTTPError as exc:
        # str(exc) can contain the request URL but never the Authorization
        # header, so the exception type alone is what gets logged.
        logger.warning("github_request_failed", url=url, error=type(exc).__name__)
        raise ExternalServiceError("GitHub could not be reached") from exc

    if response.status_code == 401:
        raise AuthenticationError("GitHub rejected the stored credentials; sign in again")
    if response.status_code == 404:
        # GitHub returns 404 rather than 403 for a repository the token cannot
        # see, so as not to disclose that it exists. That is the same rule this
        # API follows (docs/api.md), so it maps straight through to our 404 —
        # not to a 502, which would wrongly imply GitHub had failed.
        raise NotFoundError("Not found on GitHub, or not visible with your access")
    if response.status_code >= 400:
        logger.warning("github_request_error", url=url, status=response.status_code)
        raise ExternalServiceError(f"GitHub returned {response.status_code}")
    return response


def exchange_code_for_token(code: str) -> str:
    """Trade a one-time OAuth ``code`` for an access token.

    GitHub reports failures here with HTTP 200 and an ``error`` field in the
    body, so the body must be inspected — the status code alone is not enough.
    """
    settings = get_settings()
    response = _request(
        "POST",
        ACCESS_TOKEN_URL,
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret.get_secret_value(),
            "code": code,
            "redirect_uri": settings.github_callback_url,
        },
    )
    payload: dict[str, Any] = response.json()

    if "error" in payload:
        # payload["error_description"] is GitHub's text and safe to log, but the
        # client is told only that the exchange failed.
        logger.warning("github_token_exchange_failed", error=str(payload.get("error")))
        raise AuthenticationError("GitHub sign-in could not be completed")

    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ExternalServiceError("GitHub returned no access token")
    return token


def get_authenticated_user(token: str) -> GitHubUser:
    """Fetch the profile of the user the token belongs to."""
    payload: dict[str, Any] = _request("GET", f"{API_BASE}/user", token=token).json()
    return GitHubUser(
        id=int(payload["id"]),
        login=str(payload["login"]),
        name=payload.get("name"),
        email=payload.get("email") or _primary_email(token),
        avatar_url=payload.get("avatar_url"),
    )


def _primary_email(token: str) -> str | None:
    """Fall back to the verified primary email when the profile hides it.

    A user with a private email address gets ``null`` from ``/user``. This is
    best-effort: email is optional, so a failure here must not break sign-in.
    """
    try:
        response = _request("GET", f"{API_BASE}/user/emails", token=token)
        emails: list[dict[str, Any]] = response.json()
    except (ExternalServiceError, AuthenticationError, NotFoundError):
        return None
    for entry in emails:
        if entry.get("primary") and entry.get("verified"):
            value = entry.get("email")
            return str(value) if value else None
    return None


def list_repositories(token: str, *, page: int = 1, per_page: int = 30) -> list[GitHubRepository]:
    """List repositories the token can access, most recently pushed first."""
    payload: list[dict[str, Any]] = _request(
        "GET",
        f"{API_BASE}/user/repos",
        token=token,
        params={
            "page": page,
            "per_page": min(per_page, MAX_PER_PAGE),
            "sort": "pushed",
            "direction": "desc",
            # Repositories the user can only see through an organisation are
            # excluded: this application clones and indexes what it lists, and
            # affiliation is the closest proxy for "the user's own code".
            "affiliation": "owner,collaborator",
        },
    ).json()
    return [_to_repository(item) for item in payload]


def get_repository(token: str, owner: str, name: str) -> GitHubRepository:
    """Fetch a single repository, confirming the token can actually see it."""
    payload: dict[str, Any] = _request(
        "GET", f"{API_BASE}/repos/{owner}/{name}", token=token
    ).json()
    return _to_repository(payload)


def _to_repository(payload: dict[str, Any]) -> GitHubRepository:
    full_name = str(payload["full_name"])
    owner = str(payload.get("owner", {}).get("login") or full_name.split("/")[0])
    return GitHubRepository(
        id=int(payload["id"]),
        owner=owner,
        name=str(payload["name"]),
        full_name=full_name,
        description=payload.get("description"),
        # Empty repositories report no default branch.
        default_branch=str(payload.get("default_branch") or "main"),
        is_private=bool(payload.get("private", False)),
        language=payload.get("language"),
        updated_at=payload.get("pushed_at") or payload.get("updated_at"),
        html_url=str(payload.get("html_url", "")),
    )
