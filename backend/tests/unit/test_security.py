"""Session signing and token-at-rest encryption.

These are the controls standing between a database dump and usable GitHub
credentials, and between a forged cookie and someone else's account, so each
one is asserted rather than assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.security import (
    ConfigurationError,
    create_session_token,
    decrypt_token,
    encrypt_token,
    new_oauth_state,
    read_session_token,
)


def test_token_encryption_roundtrip() -> None:
    ciphertext = encrypt_token("gho_secretaccesstoken")
    assert decrypt_token(ciphertext) == "gho_secretaccesstoken"


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    """The stored value must be opaque — a database dump must not reveal it."""
    assert "gho_secretaccesstoken" not in encrypt_token("gho_secretaccesstoken")


def test_encryption_is_not_deterministic() -> None:
    """Equal tokens must not produce equal ciphertext, or storage leaks equality."""
    assert encrypt_token("same-token") != encrypt_token("same-token")


def test_decrypt_fails_after_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    ciphertext = encrypt_token("gho_secretaccesstoken")

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "a-completely-different-key-0123456789abcdef")
    get_settings.cache_clear()

    with pytest.raises(AuthenticationError):
        decrypt_token(ciphertext)


def test_missing_encryption_key_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing secret must fail loudly, never fall back to a default."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError):
        encrypt_token("anything")


def test_session_token_roundtrip() -> None:
    token = create_session_token("11111111-2222-3333-4444-555555555555")
    assert read_session_token(token) == "11111111-2222-3333-4444-555555555555"


def test_expired_session_is_rejected() -> None:
    issued = datetime.now(UTC) - timedelta(days=365)
    token = create_session_token("some-user", now=issued)

    with pytest.raises(AuthenticationError):
        read_session_token(token)


def test_tampered_session_is_rejected() -> None:
    token = create_session_token("11111111-2222-3333-4444-555555555555")
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{signature[:-4]}AAAA"

    with pytest.raises(AuthenticationError):
        read_session_token(forged)


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged = jwt.encode(
        {
            "sub": "attacker",
            "iss": "aisep",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        "not-the-real-secret-0123456789abcdefghij",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError):
        read_session_token(forged)


def test_unsigned_token_is_rejected() -> None:
    """The classic ``alg: none`` attack: a token with the signature stripped."""
    forged = jwt.encode(
        {
            "sub": "attacker",
            "iss": "aisep",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(AuthenticationError):
        read_session_token(forged)


def test_token_from_another_issuer_is_rejected() -> None:
    """A token signed with the same secret by another system is not a session."""
    secret = get_settings().session_secret.get_secret_value()
    foreign = jwt.encode(
        {
            "sub": "someone",
            "iss": "some-other-service",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError):
        read_session_token(foreign)


def test_short_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A guessable secret must fail closed, not sign tokens anyway."""
    monkeypatch.setenv("SESSION_SECRET", "short")
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError):
        create_session_token("some-user")


def test_oauth_state_is_unguessable_and_unique() -> None:
    states = {new_oauth_state() for _ in range(100)}
    assert len(states) == 100
    assert all(len(state) >= 32 for state in states)
