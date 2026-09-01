"""Session signing and encryption of GitHub access tokens at rest.

Two distinct secrets, deliberately not shared:

``SESSION_SECRET``
    Signs (HS256) the session cookie. Compromise lets an attacker forge a
    session.
``TOKEN_ENCRYPTION_KEY``
    Encrypts GitHub access tokens before they are written to the database, so a
    database dump alone does not yield usable GitHub credentials.

Both are required before the auth routes will serve a request. A missing secret
raises :class:`ConfigurationError` rather than falling back to a default — a
default here would be a silent, permanent security hole.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings
from app.core.errors import AppError, AuthenticationError

_ALGORITHM: Final = "HS256"
# Identifies tokens minted by this application, so a token borrowed from
# another system signed with the same secret cannot be replayed as a session.
_ISSUER: Final = "aisep"


class ConfigurationError(AppError):
    """A required secret or setting is missing.

    500 rather than 401: the caller did nothing wrong, the deployment is
    misconfigured. The message names the setting but never its value.
    """

    status_code = 500
    code = "internal_error"


# A 32-byte secret, as ``openssl rand -base64 32`` produces, is the documented
# minimum for HS256 (RFC 7518 §3.2). Enforced rather than warned about: a short
# secret is brute-forceable, and nothing here degrades gracefully without one.
_MIN_SECRET_LENGTH: Final = 32


def _require(value: str, setting: str, *, min_length: int = 1) -> str:
    if not value:
        raise ConfigurationError(f"{setting} is not configured; authentication is unavailable")
    if len(value) < min_length:
        raise ConfigurationError(
            f"{setting} must be at least {min_length} characters; "
            "generate one with: openssl rand -base64 32"
        )
    return value


def _fernet(settings: Settings) -> Fernet:
    """Build the Fernet cipher from ``TOKEN_ENCRYPTION_KEY``.

    Fernet demands a 32-byte urlsafe-base64 key. ``openssl rand -base64 32``
    produces standard base64, which is the documented way to generate this
    value, so the raw key material is hashed to a fixed 32 bytes and re-encoded.
    That accepts any key format without ever silently truncating one.
    """
    raw = _require(
        settings.token_encryption_key.get_secret_value(),
        "TOKEN_ENCRYPTION_KEY",
        min_length=_MIN_SECRET_LENGTH,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plaintext: str) -> str:
    """Encrypt a GitHub access token for storage."""
    return _fernet(get_settings()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a stored GitHub access token.

    A token that fails to decrypt — usually because ``TOKEN_ENCRYPTION_KEY``
    was rotated — is treated as an invalid session rather than a server error:
    the user simply has to sign in again.
    """
    try:
        return _fernet(get_settings()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise AuthenticationError("Stored credentials could not be read; sign in again") from exc


def create_session_token(user_id: str, *, now: datetime | None = None) -> str:
    """Mint a signed session token for ``user_id``."""
    settings = get_settings()
    secret = _require(
        settings.session_secret.get_secret_value(), "SESSION_SECRET", min_length=_MIN_SECRET_LENGTH
    )
    issued = now or datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iss": _ISSUER,
        "iat": issued,
        "exp": issued + timedelta(seconds=settings.session_ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def read_session_token(token: str) -> str:
    """Verify a session token and return its subject.

    ``algorithms`` is pinned to a single value: accepting the token's own ``alg``
    header would allow the ``none`` algorithm and signature stripping.
    """
    secret = _require(
        get_settings().session_secret.get_secret_value(),
        "SESSION_SECRET",
        min_length=_MIN_SECRET_LENGTH,
    )
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        # The reason is logged by the caller; the client is told only that the
        # session is unusable, so token internals are not probed via responses.
        raise AuthenticationError("Session is invalid or has expired") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("Session is invalid or has expired")
    return subject


def new_oauth_state() -> str:
    """Generate an unguessable CSRF state value for the OAuth round trip."""
    return secrets.token_urlsafe(32)
