"""GitHub OAuth sign-in, session issuance, and sign-out.

The backend owns the whole OAuth round trip (ADR-009). The browser never sees
the client secret or the GitHub access token: it receives only an HttpOnly
session cookie, and the access token is encrypted before it reaches the
database.

Flow::

    GET  /auth/github/login     -> 307 to github.com, sets a state cookie
    GET  /auth/github/callback  -> exchanges the code, sets the session cookie,
                                   redirects to the frontend
    GET  /auth/me               -> the signed-in user
    POST /auth/logout           -> clears the session cookie
"""

from __future__ import annotations

import secrets
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import ConfigurationError, create_session_token, new_oauth_state
from app.schemas.auth import UserResponse
from app.services import github
from app.services.users import upsert_from_github

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

_STATE_COOKIE: Final = "aisep_oauth_state"
# The state cookie only has to survive one redirect out to GitHub and back.
_STATE_TTL_SECONDS: Final = 600


def _cookie_kwargs() -> dict[str, Any]:
    settings = get_settings()
    return {
        "httponly": True,
        # Lax, not Strict: the cookie must be sent on GitHub's top-level
        # redirect back to the callback, which Strict would suppress.
        "samesite": "lax",
        # Plain HTTP is used for local development; anything else must be TLS.
        "secure": settings.is_production,
        "path": "/",
    }


def _require_oauth_config() -> None:
    settings = get_settings()
    if not settings.github_client_id or not settings.github_client_secret.get_secret_value():
        raise ConfigurationError(
            "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are not configured; "
            "GitHub sign-in is unavailable"
        )


def _frontend_redirect(path: str = "/", **params: str) -> RedirectResponse:
    base = get_settings().frontend_url.rstrip("/")
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{base}{path}{query}", status_code=status.HTTP_303_SEE_OTHER)


@router.get(
    "/github/login",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Begin GitHub sign-in",
)
def github_login() -> RedirectResponse:
    """Redirect the browser to GitHub's consent screen."""
    _require_oauth_config()
    state = new_oauth_state()

    response = RedirectResponse(
        github.authorize_url(state), status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    # The state is held in an HttpOnly cookie and compared on the way back.
    # Without this check an attacker could feed the user their own OAuth code
    # and bind the victim's session to the attacker's GitHub account.
    response.set_cookie(_STATE_COOKIE, state, max_age=_STATE_TTL_SECONDS, **_cookie_kwargs())
    return response


@router.get("/github/callback", summary="Complete GitHub sign-in")
def github_callback(
    request: Request,
    session: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle GitHub's redirect: verify state, exchange the code, sign in.

    Failures redirect back to the frontend with an ``auth_error`` parameter
    rather than rendering an error page: the user is mid-navigation in a
    browser, and the frontend owns the presentation of that state.
    """
    expected_state = request.cookies.get(_STATE_COOKIE)

    if error:
        # The user declined consent, or GitHub rejected the request.
        logger.info("github_oauth_denied", error=error)
        return _clear_state(_frontend_redirect(auth_error="access_denied"))

    # Compared in constant time, and only after confirming both sides exist —
    # a missing cookie must fail closed rather than compare equal to a missing
    # query parameter.
    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        logger.warning("github_oauth_state_mismatch", had_cookie=expected_state is not None)
        return _clear_state(_frontend_redirect(auth_error="invalid_state"))

    if not code:
        return _clear_state(_frontend_redirect(auth_error="missing_code"))

    try:
        _require_oauth_config()
        access_token = github.exchange_code_for_token(code)
        profile = github.get_authenticated_user(access_token)
        user = upsert_from_github(session, profile, access_token)
        user_id = str(user.id)
    except AppError as exc:
        # exc.message is safe to log; the browser gets only a stable code.
        logger.warning("github_oauth_failed", code=exc.code, message=exc.message)
        return _clear_state(_frontend_redirect(auth_error=exc.code))

    logger.info("user_signed_in", user_id=user_id, login=profile.login)

    response = _clear_state(_frontend_redirect("/repositories"))
    response.set_cookie(
        get_settings().session_cookie_name,
        create_session_token(user_id),
        max_age=get_settings().session_ttl_seconds,
        **_cookie_kwargs(),
    )
    return response


def _clear_state(response: RedirectResponse) -> RedirectResponse:
    """Expire the one-shot OAuth state cookie so it cannot be replayed."""
    response.delete_cookie(_STATE_COOKIE, path="/")
    return response


@router.get("/me", response_model=UserResponse, summary="The signed-in user")
def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
def logout(response: Response) -> Response:
    """Clear the session cookie.

    Deliberately unauthenticated: signing out with an already-invalid session
    should succeed quietly rather than return 401. The stored GitHub token is
    left in place so re-signing in does not require re-consent.
    """
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
