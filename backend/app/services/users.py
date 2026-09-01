"""User persistence for the OAuth flow."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import encrypt_token
from app.models.user import User
from app.services.github import GitHubUser


def upsert_from_github(session: Session, profile: GitHubUser, access_token: str) -> User:
    """Create or refresh the local user record for a GitHub profile.

    Matched on ``github_id``, never on login: GitHub logins can be renamed, and
    a renamed account must stay the same user rather than becoming a new one
    that has lost its connected repositories.
    """
    user = session.execute(
        select(User).where(User.github_id == profile.id)
    ).scalar_one_or_none()

    if user is None:
        user = User(github_id=profile.id, login=profile.login)
        session.add(user)

    # Profile fields are refreshed on every sign-in so a renamed account or a
    # changed avatar does not go stale.
    user.login = profile.login
    user.name = profile.name
    user.email = profile.email
    user.avatar_url = profile.avatar_url
    user.github_token_encrypted = encrypt_token(access_token)

    session.flush()
    return user
