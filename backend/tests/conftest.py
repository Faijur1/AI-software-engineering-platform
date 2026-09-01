from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # raise_server_exceptions=False so the registered 500 handler is exercised
    # instead of the exception propagating into the test.
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
