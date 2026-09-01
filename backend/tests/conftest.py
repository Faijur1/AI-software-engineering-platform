from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_db
from app.main import create_app


@pytest.fixture(autouse=True)
def _test_secrets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test deterministic, obviously-fake credentials.

    Settings are cached process-wide, so the cache is cleared on both sides of
    the test: entering, so these values take effect, and leaving, so a test that
    changes configuration cannot leak into the next one.
    """
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-not-a-real-one-0123456789")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key-not-real-0123456789")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    # raise_server_exceptions=False so the registered 500 handler is exercised
    # instead of the exception propagating into the test.
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


class EmptySession:
    """A Session stand-in that holds no rows.

    ``current_user`` must load the user from the database on every request, so
    the session dependency is always resolved — even for a request that is
    about to be rejected as unauthenticated. This lets the rejection paths be
    tested without a live Postgres, while still exercising the real lookup.
    """

    def get(self, *_args: object, **_kwargs: object) -> None:
        return None


@pytest.fixture
def anonymous_client() -> Iterator[TestClient]:
    """A client backed by an empty database, for unauthenticated-path tests."""
    app = create_app()

    def _empty_session() -> Iterator[EmptySession]:
        yield EmptySession()

    app.dependency_overrides[get_db] = _empty_session
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
