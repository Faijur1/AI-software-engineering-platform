"""Every documented route answers instead of raising.

This exists because `GET /evaluations` returned 500 for several commits and
nothing noticed. The endpoint had unit tests, and they passed: they exercised
`_load_latest` against fixtures that shared none of the structure of a real
report directory. Nothing called the route against the application as it is
actually assembled.

So the assertion here is deliberately weak, and its strength is elsewhere. It
does not pin what any endpoint returns -- the tests next to each route do that.
It pins two things no per-route test can:

1. **Nothing raises.** A 500 is the application failing to handle its own
   request. 4xx is a fine answer, and so is 502 when a dependency is genuinely
   unreachable; an unhandled exception never is.
2. **Every error is a well-formed envelope**, as ``docs/api.md`` specifies.

The route list comes from the OpenAPI spec rather than from a literal in this
file, so a route added tomorrow is covered without anyone remembering to add it
here. That is the property that would have caught the original bug: the fault
was in a route nobody thought to re-check.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.security import create_session_token
from app.main import create_app
from app.models.user import User

pytestmark = pytest.mark.integration

SMOKE_GITHUB_ID = 900_000_901

# Well-formed but absent. Every parameterised route should answer "no such
# thing", which is a handled 404, rather than failing to parse or blowing up.
ABSENT_ID = str(uuid.uuid4())
ABSENT_TRACE = uuid.uuid4().hex

# Endpoints that would leave the process or reach outside it. The smoke test
# exercises reachability, not side effects.
SKIP: frozenset[tuple[str, str]] = frozenset()

# /health is exempt from the error-envelope rule, and only from that rule. Its
# 503 is a report rather than a failure: the body is the documented health
# schema, and a client reads which dependency is down out of it. Wrapping that
# in an error envelope would hide the one thing the response exists to carry.
# It is still required not to return 500.
ENVELOPE_EXEMPT: frozenset[tuple[str, str]] = frozenset({("GET", "/health")})


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the real GitHub.

    Routes that call out get a transport that refuses everything, so they take
    their own error path. That path returning a clean 502 is part of what is
    being checked.
    """
    original = httpx.Client

    def factory(**kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return original(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(404, json={"message": "Not Found"})
            )
        )

    monkeypatch.setattr(httpx, "Client", factory)


@pytest.fixture
def signed_in(no_network: None) -> Iterator[TestClient]:
    """A client holding a real session cookie for a real user row.

    Signed in directly rather than through the OAuth round trip: this test is
    about every *other* route, and routing sign-in through a stubbed GitHub
    would make an unrelated failure look like a routing failure.
    """

    def purge() -> None:
        with session_scope() as session:
            session.execute(delete(User).where(User.github_id == SMOKE_GITHUB_ID))

    purge()
    with session_scope() as session:
        user = User(github_id=SMOKE_GITHUB_ID, login="smoke", email=None)
        session.add(user)
        session.flush()
        user_id = str(user.id)

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        client.cookies.set(
            get_settings().session_cookie_name, create_session_token(user_id)
        )
        yield client

    purge()


def _documented_routes(client: TestClient) -> list[tuple[str, str]]:
    spec: dict[str, Any] = client.app.openapi()  # type: ignore[attr-defined]
    routes = [
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method in ("get", "post", "put", "patch", "delete")
    ]
    return sorted(set(routes) - SKIP)


def _fill(path: str) -> str:
    return (
        path.replace("{repository_id}", ABSENT_ID)
        .replace("{run_id}", ABSENT_ID)
        .replace("{patch_id}", ABSENT_ID)
        .replace("{job_id}", ABSENT_ID)
        .replace("{trace_id}", ABSENT_TRACE)
    )


def test_every_documented_route_is_reachable(signed_in: TestClient) -> None:
    """No route answers 500, and every error is the documented envelope."""
    routes = _documented_routes(signed_in)
    assert routes, "no routes discovered; the spec or the filter is wrong"

    failures: list[str] = []
    for method, path in routes:
        url = _fill(path)
        response = signed_in.request(
            method,
            url,
            json={} if method in ("POST", "PUT", "PATCH") else None,
            follow_redirects=False,
        )

        if response.status_code == 500:
            failures.append(f"{method} {path} -> 500 {response.text[:160]}")
            continue

        if response.status_code < 400 or (method, path) in ENVELOPE_EXEMPT:
            continue

        # Errors must be the shape docs/api.md promises, so a client can always
        # read a code out of a failure.
        try:
            body = response.json()
        except ValueError:
            failures.append(f"{method} {path} -> {response.status_code}, body not JSON")
            continue
        if not isinstance(body.get("error"), dict) or not body["error"].get("code"):
            failures.append(
                f"{method} {path} -> {response.status_code}, malformed envelope: "
                f"{response.text[:120]}"
            )

    assert not failures, "\n".join(failures)


def test_an_unparameterised_get_is_actually_served(signed_in: TestClient) -> None:
    """A guard on the guard.

    If the spec were empty, or every route 404'd because none were mounted, the
    test above would pass while asserting nothing. So at least one known route
    must return real data.
    """
    response = signed_in.get("/repositories")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_the_evaluations_route_is_covered_by_the_sweep(signed_in: TestClient) -> None:
    """The specific regression, named so it cannot quietly leave the sweep."""
    assert ("GET", "/evaluations") in _documented_routes(signed_in)

    response = signed_in.get("/evaluations")

    # 200 with a report, or 404 when none has been run. Never 500.
    assert response.status_code in (200, 404), response.text
