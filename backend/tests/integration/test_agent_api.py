"""Agent, trace and patch endpoints.

The queue is stubbed so no worker is needed; what is under test is that the API
accepts work correctly, never blocks on it, and never leaks another user's run,
trace or patch.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import session_scope
from app.models.agent import AgentRun, AgentStatus, Patch, PatchStatus
from app.models.chunk import ChunkKind, CodeChunk
from app.models.file import File
from app.models.user import User
from app.routes import agents as agents_route
from app.routes.auth import _STATE_COOKIE

pytestmark = pytest.mark.integration

ALICE = 900_000_501
BOB = 900_000_502

REPO = {
    "id": 900_000_600,
    "name": "sample",
    "full_name": "alice/sample",
    "owner": {"login": "alice"},
    "description": None,
    "default_branch": "main",
    "private": False,
    "language": "Python",
    "pushed_at": "2026-08-30T10:00:00Z",
    "html_url": "https://github.com/alice/sample",
}

DIFF = (
    "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n-    return a - b\n+    return a + b\n"
)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    def purge() -> None:
        with session_scope() as session:
            session.execute(delete(User).where(User.github_id.in_([ALICE, BOB])))

    purge()
    yield
    purge()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, bool]]:
    recorded: list[tuple[uuid.UUID, bool]] = []

    class StubQueue:
        def enqueue_agent_run(self, run_id: uuid.UUID, *, allow_tests: bool) -> None:
            recorded.append((run_id, allow_tests))

    monkeypatch.setattr(agents_route, "get_queue", lambda: StubQueue())
    return recorded


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Callable[[int, str], None]:
    original = httpx.Client
    active: list[Handler] = []

    def factory(**kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return original(transport=httpx.MockTransport(lambda r: active[-1](r)))

    monkeypatch.setattr(httpx, "Client", factory)

    def install(profile_id: int, login: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/login/oauth/access_token":
                return httpx.Response(200, json={"access_token": f"gho_{login}"})
            if path == "/user":
                return httpx.Response(
                    200, json={"id": profile_id, "login": login, "email": None}
                )
            if path.startswith("/repos/"):
                return httpx.Response(200, json=REPO)
            return httpx.Response(404, json={"message": "Not Found"})

        active.append(handler)

    return install


def _sign_in(client: TestClient) -> None:
    state = client.get("/auth/github/login", follow_redirects=False).cookies[_STATE_COOKIE]
    assert (
        client.get(
            f"/auth/github/callback?code=c&state={state}", follow_redirects=False
        ).status_code
        == 303
    )


def _connect_and_index(client: TestClient) -> str:
    """Connect the repository and give it one embedded chunk."""
    created = client.post("/repositories", json={"owner": "alice", "name": "sample"})
    assert created.status_code == 201
    repository_id = uuid.UUID(created.json()["id"])

    with session_scope() as session:
        source = File(
            repository_id=repository_id,
            path="src/calc.py",
            language="python",
            content_hash="h" * 64,
            commit_sha="c" * 40,
            size_bytes=40,
        )
        session.add(source)
        session.flush()
        session.add(
            CodeChunk(
                file_id=source.id,
                repository_id=repository_id,
                content="def add(a, b):\n    return a - b\n",
                symbol="add",
                kind=ChunkKind.function,
                start_line=1,
                end_line=2,
                chunk_hash="k" * 64,
                embedding=[0.1] * 768,
                embedding_model="test",
            )
        )
    return str(repository_id)


# --- starting a run ---------------------------------------------------------


def test_starting_a_run_returns_202_and_a_record_to_poll(
    client: TestClient,
    github: Callable[[int, str], None],
    queued: list[tuple[uuid.UUID, bool]],
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)

    response = client.post(
        "/agents/run",
        json={"repository_id": repository_id, "task": "Where is add defined?"},
    )

    # 202: accepted, not performed. A run is minutes of work.
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["iterations"] == 0
    assert body["max_iterations"] == 8
    assert len(body["trace_id"]) == 32
    assert queued == [(uuid.UUID(body["id"]), False)]


def test_sandbox_permission_is_off_unless_asked_for(
    client: TestClient,
    github: Callable[[int, str], None],
    queued: list[tuple[uuid.UUID, bool]],
) -> None:
    """Running a repository's tests is a much larger capability than reading it."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)

    client.post("/agents/run", json={"repository_id": repository_id, "task": "t"})
    client.post(
        "/agents/run",
        json={"repository_id": repository_id, "task": "t", "allow_tests": True},
    )

    assert [allow for _, allow in queued] == [False, True]


def test_the_iteration_cap_cannot_be_raised_arbitrarily(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)

    response = client.post(
        "/agents/run",
        json={"repository_id": repository_id, "task": "t", "max_iterations": 5000},
    )

    assert response.status_code == 422


def test_running_against_an_unindexed_repository_says_so(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    created = client.post("/repositories", json={"owner": "alice", "name": "sample"})

    response = client.post(
        "/agents/run", json={"repository_id": created.json()["id"], "task": "t"}
    )

    assert response.status_code == 422
    assert "Index it first" in response.json()["error"]["message"]


def test_running_against_another_users_repository_is_not_found(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    alice_repo = _connect_and_index(client)

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)
        response = bob.post(
            "/agents/run", json={"repository_id": alice_repo, "task": "t"}
        )

    assert response.status_code == 404
    assert queued == []


# --- reading runs and traces ------------------------------------------------


def test_a_run_reports_its_tool_calls_including_rejected_ones(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """Rejections are how a run that went nowhere becomes diagnosable."""
    from app.models.agent import ToolRun, ToolStatus

    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)
    run_id = client.post(
        "/agents/run", json={"repository_id": repository_id, "task": "t"}
    ).json()["id"]

    with session_scope() as session:
        for iteration, (name, status) in enumerate(
            [("search_code", ToolStatus.succeeded), ("nope", ToolStatus.rejected)], start=1
        ):
            session.add(
                ToolRun(
                    agent_run_id=uuid.UUID(run_id),
                    iteration=iteration,
                    tool_name=name,
                    status=status,
                    duration_ms=5,
                )
            )

    body = client.get(f"/agents/runs/{run_id}").json()

    assert [c["tool_name"] for c in body["tool_runs"]] == ["search_code", "nope"]
    assert body["tool_runs"][1]["status"] == "rejected"


def test_another_users_run_is_not_readable(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)
    run_id = client.post(
        "/agents/run", json={"repository_id": repository_id, "task": "t"}
    ).json()["id"]

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)
        assert bob.get(f"/agents/runs/{run_id}").status_code == 404


def test_a_trace_is_returned_in_sequence_order(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    from datetime import UTC, datetime

    from app.models.agent import Event

    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)
    started = client.post(
        "/agents/run", json={"repository_id": repository_id, "task": "t"}
    ).json()
    trace_id = started["trace_id"]

    with session_scope() as session:
        # Inserted out of order on purpose: ordering must come from sequence.
        ordering = [(3, "agent.completed"), (1, "agent.started"), (2, "tool.started")]
        for sequence, event_type in ordering:
            session.add(
                Event(
                    trace_id=trace_id,
                    sequence=sequence,
                    event_type=event_type,
                    component="agent",
                    ts=datetime.now(UTC),
                )
            )

    body = client.get(f"/traces/{trace_id}").json()

    assert [e["sequence"] for e in body["events"]] == [1, 2, 3]
    assert body["events"][0]["event_type"] == "agent.started"


def test_a_guessed_trace_id_discloses_nothing(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)
    trace_id = client.post(
        "/agents/run", json={"repository_id": repository_id, "task": "t"}
    ).json()["trace_id"]

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)
        assert bob.get(f"/traces/{trace_id}").status_code == 404


# --- patches ----------------------------------------------------------------


def _propose(client: TestClient, run_id: str, diff: str = DIFF) -> httpx.Response:
    return client.post(
        "/patches", json={"agent_run_id": run_id, "diff": diff, "validate_in_sandbox": False}
    )


def _start_run(client: TestClient, repository_id: str) -> str:
    return str(
        client.post(
            "/agents/run", json={"repository_id": repository_id, "task": "fix add"}
        ).json()["id"]
    )


def test_a_proposed_patch_starts_unvalidated_and_unapproved(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))

    response = _propose(client, run_id)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "proposed"
    # Null means not validated, and must never be read as passed.
    assert body["validated"] is None
    assert body["approved_by"] is None


def test_a_malformed_diff_is_refused_rather_than_stored(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """Keeping an unusable patch only invites someone to apply it later."""
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))

    response = _propose(client, run_id, diff="I think you should change add()")

    assert response.status_code == 422
    with session_scope() as session:
        assert session.execute(select(Patch)).scalars().first() is None


def test_a_diff_escaping_the_repository_is_refused(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))
    escaping = (
        "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    )

    assert _propose(client, run_id, diff=escaping).status_code == 422


def test_approval_records_who_and_when(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """An approval with nobody attached to it is not an approval."""
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))
    patch_id = _propose(client, run_id).json()["id"]

    body = client.post(f"/patches/{patch_id}/approve", json={"approve": True}).json()

    assert body["status"] == "approved"
    assert body["approved_by"] is not None
    assert body["approved_at"] is not None


def test_a_patch_can_be_rejected(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))
    patch_id = _propose(client, run_id).json()["id"]

    body = client.post(f"/patches/{patch_id}/approve", json={"approve": False}).json()

    assert body["status"] == "rejected"
    assert body["approved_by"] is not None


def test_deciding_twice_is_refused(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """Re-approving would overwrite who decided and when."""
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))
    patch_id = _propose(client, run_id).json()["id"]
    client.post(f"/patches/{patch_id}/approve", json={"approve": True})

    second = client.post(f"/patches/{patch_id}/approve", json={"approve": True})

    assert second.status_code == 409


def test_an_unvalidated_patch_may_still_be_approved_but_the_record_shows_it(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """Refusing would be the tool overriding the human; hiding it would be worse."""
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))
    patch_id = _propose(client, run_id).json()["id"]

    body = client.post(f"/patches/{patch_id}/approve", json={"approve": True}).json()

    assert body["status"] == "approved"
    assert body["validated"] is None


def test_another_users_patch_is_not_readable_or_approvable(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))
    patch_id = _propose(client, run_id).json()["id"]

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)
        assert bob.get(f"/patches/{patch_id}").status_code == 404
        assert (
            bob.post(f"/patches/{patch_id}/approve", json={"approve": True}).status_code
            == 404
        )

    # And Alice's patch is untouched.
    with session_scope() as session:
        patch = session.get(Patch, uuid.UUID(patch_id))
        assert patch is not None
        assert patch.status is PatchStatus.proposed


def test_agent_endpoints_require_a_session(anonymous_client: TestClient) -> None:
    for method, path, payload in (
        ("POST", "/agents/run", {"repository_id": str(uuid.uuid4()), "task": "t"}),
        ("GET", f"/agents/runs/{uuid.uuid4()}", None),
        ("GET", f"/traces/{'a' * 32}", None),
        ("GET", f"/patches/{uuid.uuid4()}", None),
        ("POST", f"/patches/{uuid.uuid4()}/approve", {"approve": True}),
    ):
        response = anonymous_client.request(method, path, json=payload)
        assert response.status_code == 401, path


def test_a_run_is_created_in_the_queued_state(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    run_id = _start_run(client, _connect_and_index(client))

    with session_scope() as session:
        run = session.get(AgentRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status is AgentStatus.queued


def test_runs_can_be_listed_newest_first(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """The replay picker needs a list; ordering is what makes it usable."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)

    first = _start_run(client, repository_id)
    second = _start_run(client, repository_id)

    listed = client.get("/agents/runs").json()

    assert [run["id"] for run in listed][:2] == [second, first]


def test_the_run_listing_is_bounded(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """One request must not be able to pull an unbounded history."""
    github(ALICE, "alice")
    _sign_in(client)

    assert client.get("/agents/runs?limit=500").status_code == 422


def test_the_run_listing_shows_only_your_own_runs(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    alice_run = _start_run(client, _connect_and_index(client))

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)
        listed = bob.get("/agents/runs").json()

    assert alice_run not in [run["id"] for run in listed]


def test_listing_runs_requires_a_session(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/agents/runs").status_code == 401


def test_trace_events_carry_the_timing_replay_needs(
    client: TestClient, github: Callable[[int, str], None], queued: list[Any]
) -> None:
    """Replay derives its pacing from recorded timestamps, so they must be
    present and ordered -- an invented interval would make the replay a
    dramatisation rather than a record."""
    from datetime import UTC, datetime, timedelta

    from app.models.agent import Event

    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect_and_index(client)
    trace_id = client.post(
        "/agents/run", json={"repository_id": repository_id, "task": "t"}
    ).json()["trace_id"]

    base = datetime.now(UTC)
    with session_scope() as session:
        for sequence, offset in [(1, 0), (2, 3), (3, 11)]:
            session.add(
                Event(
                    trace_id=trace_id,
                    sequence=sequence,
                    event_type="tool.started",
                    component="tool",
                    ts=base + timedelta(seconds=offset),
                )
            )

    events = client.get(f"/traces/{trace_id}").json()["events"]

    assert [e["sequence"] for e in events] == [1, 2, 3]
    stamps = [datetime.fromisoformat(e["ts"]) for e in events]
    # Real, increasing gaps -- the intervals the replay will scale.
    assert (stamps[1] - stamps[0]).total_seconds() == 3
    assert (stamps[2] - stamps[1]).total_seconds() == 8
