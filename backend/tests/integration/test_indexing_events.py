"""Indexing produces a trace, recorded by a consumer separate from the indexer.

This is the second consumer ADR-004 makes the precondition for adopting Kafka.
What is asserted here is not "some rows were written" but the properties that
make it a real consumer: it records the run's own sequence numbers, it writes
counts rather than repository content, and the trace is reachable through the
same authorised endpoint the agent uses — without being reachable by anyone
else.
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
from app.events import DomainEvent, InProcessEventBus, TraceRecorder
from app.events import types as events
from app.models.agent import Event
from app.models.job import Job, JobStatus, JobType
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.integration

OWNER = 900_000_801


@pytest.fixture
def repository() -> Iterator[uuid.UUID]:
    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == OWNER))
    with session_scope() as session:
        user = User(github_id=OWNER, login="events-owner", email=None)
        session.add(user)
        session.flush()
        repo = Repository(
            user_id=user.id,
            github_id=900_000_802,
            owner="events-owner",
            name="sample",
            default_branch="main",
        )
        session.add(repo)
        session.flush()
        repository_id = repo.id

    yield repository_id

    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == OWNER))


def _emit_run(trace_id: str, job_id: str) -> None:
    """Publish one full lifecycle through the real bus and recorder."""
    bus = InProcessEventBus()
    bus.subscribe(TraceRecorder(trace_id))
    lifecycle: list[tuple[str, dict[str, Any]]] = [
        (events.JOB_STARTED, {"repository": "events-owner/sample", "ref": "main"}),
        (events.SNAPSHOT_FETCHED, {"commit": "a" * 40}),
        (events.FILES_DISCOVERED, {"files_seen": 9, "files_indexed": 4}),
        (events.CHUNKS_WRITTEN, {"chunks_created": 21, "languages": {"python": 4}}),
        (events.EMBEDDINGS_WRITTEN, {}),
        (events.JOB_COMPLETED, {"commit": "a" * 40}),
    ]
    for sequence, (event_type, payload) in enumerate(lifecycle, start=1):
        bus.publish(
            DomainEvent(
                event_type=event_type, key=job_id, sequence=sequence, payload=payload
            )
        )


def _events_for(trace_id: str) -> list[Event]:
    with session_scope() as session:
        return list(
            session.execute(
                select(Event).where(Event.trace_id == trace_id).order_by(Event.sequence)
            ).scalars()
        )


def test_a_run_is_recorded_in_order_under_its_trace(repository: uuid.UUID) -> None:
    trace_id = uuid.uuid4().hex
    _emit_run(trace_id, str(uuid.uuid4()))

    recorded = _events_for(trace_id)

    assert [e.event_type for e in recorded] == [
        events.JOB_STARTED,
        events.SNAPSHOT_FETCHED,
        events.FILES_DISCOVERED,
        events.CHUNKS_WRITTEN,
        events.EMBEDDINGS_WRITTEN,
        events.JOB_COMPLETED,
    ]
    # The producer's numbering, not the consumer's. A consumer that restarted
    # mid-run would otherwise renumber the tail of the trace.
    assert [e.sequence for e in recorded] == [1, 2, 3, 4, 5, 6]


def test_recorded_payloads_carry_counts_not_repository_content(
    repository: uuid.UUID,
) -> None:
    """The rule that the log's durability makes necessary.

    An event log is retained and replayable, so repository content written into
    it would outlive every control governing where that code may go
    (docs/security.md). Counts and identifiers are safe; file bodies are not.
    """
    trace_id = uuid.uuid4().hex
    _emit_run(trace_id, str(uuid.uuid4()))

    for event in _events_for(trace_id):
        for key, value in event.event_metadata.items():
            assert key not in {"content", "source", "text", "diff", "token"}
            if isinstance(value, str):
                # A commit sha or a repository name is short; a file is not.
                assert len(value) <= 128, f"{key} looks like content, not a count"


def test_the_indexer_still_finishes_when_the_recorder_is_broken(
    repository: uuid.UUID,
) -> None:
    """The work is the point; the trace is commentary on it."""

    class Broken:
        @property
        def name(self) -> str:
            return "broken"

        def handle(self, event: DomainEvent) -> None:
            raise RuntimeError("recorder is down")

    bus = InProcessEventBus()
    bus.subscribe(Broken())

    bus.publish(DomainEvent(event_type=events.JOB_STARTED, key="j", sequence=1))



def _job_with_trace(repository_id: uuid.UUID, trace_id: str) -> uuid.UUID:
    """A finished indexing job carrying the trace, which is what authorises it."""
    with session_scope() as session:
        job = Job(
            type=JobType.index_repository,
            status=JobStatus.succeeded,
            repository_id=repository_id,
            trace_id=trace_id,
        )
        session.add(job)
        session.flush()
        return job.id


# --- the trace has to be reachable, and only by its owner -------------------
#
# Signing in through the stubbed OAuth flow rather than inserting a user row,
# because the property under test is the endpoint's authorisation and that is
# reached through a real session.

ALICE = 900_000_803
BOB = 900_000_804
GITHUB_REPO = {
    "id": 900_000_805,
    "name": "sample",
    "full_name": "alice/sample",
    "owner": {"login": "alice"},
    "description": None,
    "default_branch": "main",
    "private": False,
    "language": "Python",
    "pushed_at": "2026-09-01T10:00:00Z",
    "html_url": "https://github.com/alice/sample",
}


@pytest.fixture(autouse=True)
def _purge_signins() -> Iterator[None]:
    def purge() -> None:
        with session_scope() as session:
            session.execute(delete(User).where(User.github_id.in_([ALICE, BOB])))

    purge()
    yield
    purge()


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Callable[[int, str], None]:
    original = httpx.Client
    active: list[Any] = []

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
                return httpx.Response(200, json=GITHUB_REPO)
            return httpx.Response(404, json={"message": "Not Found"})

        active.append(handler)

    return install


def _sign_in(client: TestClient) -> None:
    from app.routes.auth import _STATE_COOKIE

    state = client.get("/auth/github/login", follow_redirects=False).cookies[_STATE_COOKIE]
    assert (
        client.get(
            f"/auth/github/callback?code=c&state={state}", follow_redirects=False
        ).status_code
        == 303
    )


def _connect(client: TestClient) -> uuid.UUID:
    created = client.post("/repositories", json={"owner": "alice", "name": "sample"})
    assert created.status_code == 201, created.text
    return uuid.UUID(created.json()["id"])


def test_an_indexing_trace_is_readable_by_the_repository_owner(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    """Before this, the endpoint authorised only through agent runs, so an
    indexing trace existed in the table and the endpoint answered 404."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)

    trace_id = uuid.uuid4().hex
    _emit_run(trace_id, str(uuid.uuid4()))
    _job_with_trace(repository_id, trace_id)

    response = client.get(f"/traces/{trace_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["event_type"] for e in body["events"]][:2] == [
        events.JOB_STARTED,
        events.SNAPSHOT_FETCHED,
    ]
    assert [e["sequence"] for e in body["events"]] == [1, 2, 3, 4, 5, 6]


def test_another_users_indexing_trace_is_reported_as_absent(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    """404 rather than 403: a guessed trace id must disclose nothing, and that
    has to hold for the new job-owned traces as well as agent ones."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)
    trace_id = uuid.uuid4().hex
    _emit_run(trace_id, str(uuid.uuid4()))
    _job_with_trace(repository_id, trace_id)
    client.post("/auth/logout")

    github(BOB, "bob")
    _sign_in(client)
    response = client.get(f"/traces/{trace_id}")

    assert response.status_code == 404


def test_reading_a_trace_requires_a_session(anonymous_client: TestClient) -> None:
    assert anonymous_client.get(f"/traces/{uuid.uuid4().hex}").status_code == 401
