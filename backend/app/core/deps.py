"""Shared FastAPI dependencies.

``current_user`` is the single place a request is turned into an authenticated
identity. Route handlers depend on it rather than reading the cookie themselves,
so no endpoint can accidentally be written without an authentication check.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AuthenticationError
from app.core.security import decrypt_token, read_session_token
from app.models.user import User

DbSession = Annotated[Session, Depends(get_db)]


def current_user(request: Request, session: DbSession) -> User:
    """Resolve the signed session cookie to a live user row.

    The user is loaded from the database on every request rather than trusted
    from the token body, so a deleted account stops working immediately instead
    of at token expiry.
    """
    cookie = request.cookies.get(get_settings().session_cookie_name)
    if not cookie:
        raise AuthenticationError("Not signed in")

    user_id = read_session_token(cookie)
    try:
        parsed = uuid.UUID(user_id)
    except ValueError as exc:
        raise AuthenticationError("Session is invalid or has expired") from exc

    user = session.get(User, parsed)
    if user is None:
        raise AuthenticationError("Session is invalid or has expired")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def github_token(user: CurrentUser) -> str:
    """Decrypt the caller's GitHub access token.

    Separate from ``current_user`` so that endpoints which only need an identity
    never decrypt a credential they have no use for.
    """
    if not user.github_token_encrypted:
        raise AuthenticationError("No GitHub credentials are stored; sign in again")
    return decrypt_token(user.github_token_encrypted)


GitHubToken = Annotated[str, Depends(github_token)]
