"""The indexing API: queueing, polling, and tenant isolation.

The queue is stubbed so no worker is required; what is under test is that the
API accepts work correctly, never blocks on it, and never leaks another user's
job.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import session_scope
from app.models.chunk import ChunkKind, CodeChunk
from app.models.file import File
from app.models.job import Job, JobStatus
from app.models.repository import IndexStatus, Repository
from app.models.user import User
from app.routes import repositories as repositories_route
from app.routes.auth import _STATE_COOKIE

pytestmark = pytest.mark.integration

ALICE = 900_000_301
BOB = 900_000_302

REPO = {
    "id": 900_000_400,
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
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Record enqueued job ids instead of putting them on Redis."""
    recorded: list[uuid.UUID] = []

    class StubQueue:
        def enqueue_index_repository(self, job_id: uuid.UUID) -> None:
            recorded.append(job_id)

    monkeypatch.setattr(repositories_route, "get_queue", lambda: StubQueue())
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
    login = client.get("/auth/github/login", follow_redirects=False)
    state = login.cookies[_STATE_COOKIE]
    assert (
        client.get(
            f"/auth/github/callback?code=c&state={state}", follow_redirects=False
        ).status_code
        == 303
    )


def _connect(client: TestClient) -> str:
    created = client.post("/repositories", json={"owner": "alice", "name": "sample"})
    assert created.status_code == 201
    return str(created.json()["id"])


def test_indexing_returns_202_and_a_job_to_poll(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)

    response = client.post(f"/repositories/{repository_id}/index")

    # 202: accepted, not performed. The request must not wait for indexing.
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["progress"] == 0
    assert body["repository_id"] == repository_id

    # And the work actually reached the queue.
    assert enqueued == [uuid.UUID(body["id"])]

    # The repository reflects the queued state for the UI.
    listed = client.get("/repositories").json()
    assert listed[0]["index_status"] == "queued"


def test_polling_the_job_reports_its_progress(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    job_id = client.post(f"/repositories/{_connect(client)}/index").json()["id"]

    # Simulate the worker advancing the job.
    with session_scope() as session:
        job = session.get(Job, uuid.UUID(job_id))
        assert job is not None
        job.status = JobStatus.running
        job.progress = 45
        job.stage = "parsing and chunking"

    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "running"
    assert body["progress"] == 45
    assert body["stage"] == "parsing and chunking"


def test_queueing_twice_returns_the_same_job(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    """Two workers writing the same chunks would corrupt the index."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)

    first = client.post(f"/repositories/{repository_id}/index").json()
    second = client.post(f"/repositories/{repository_id}/index").json()

    assert first["id"] == second["id"]
    assert len(enqueued) == 1


def test_a_finished_job_does_not_block_reindexing(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)
    first = client.post(f"/repositories/{repository_id}/index").json()

    with session_scope() as session:
        job = session.get(Job, uuid.UUID(first["id"]))
        assert job is not None
        job.status = JobStatus.succeeded

    second = client.post(f"/repositories/{repository_id}/index").json()

    assert second["id"] != first["id"]
    assert len(enqueued) == 2


def test_indexing_a_repository_you_do_not_own_is_not_found(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    alice_repo = _connect(client)

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)

        response = bob.post(f"/repositories/{alice_repo}/index")

    # 404, not 403: the repository's existence is not disclosed.
    assert response.status_code == 404
    assert enqueued == []


def test_another_users_job_is_not_readable(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    job_id = client.post(f"/repositories/{_connect(client)}/index").json()["id"]

    with TestClient(client.app, raise_server_exceptions=False) as bob:
        github(BOB, "bob")
        _sign_in(bob)
        response = bob.get(f"/jobs/{job_id}")

    assert response.status_code == 404


def test_indexing_an_unknown_repository_is_not_found(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)

    response = client.post(f"/repositories/{uuid.uuid4()}/index")

    assert response.status_code == 404
    assert enqueued == []


def test_disconnecting_a_repository_removes_its_jobs(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)
    job_id = client.post(f"/repositories/{repository_id}/index").json()["id"]

    assert client.delete(f"/repositories/{repository_id}").status_code == 204

    with session_scope() as session:
        assert session.get(Job, uuid.UUID(job_id)) is None
        assert (
            session.execute(
                select(Repository).where(Repository.id == uuid.UUID(repository_id))
            ).scalar_one_or_none()
            is None
        )


def test_index_status_starts_as_not_indexed(
    client: TestClient, github: Callable[[int, str], None]
) -> None:
    github(ALICE, "alice")
    _sign_in(client)
    _connect(client)

    assert client.get("/repositories").json()[0]["index_status"] == IndexStatus.not_indexed


def test_repository_listing_reports_real_index_counts(
    client: TestClient, github: Callable[[int, str], None], enqueued: list[uuid.UUID]
) -> None:
    """Counts come from the database, so an empty index reports zero, not a guess."""
    github(ALICE, "alice")
    _sign_in(client)
    repository_id = _connect(client)

    listed = client.get("/repositories").json()[0]
    assert listed["file_count"] == 0
    assert listed["chunk_count"] == 0
    assert listed["embedded_chunks"] == 0

    # Write one file and two chunks, only one of them embedded.
    with session_scope() as session:
        source = File(
            repository_id=uuid.UUID(repository_id),
            path="a.py",
            language="python",
            content_hash="h" * 64,
            commit_sha="c" * 40,
            size_bytes=10,
        )
        session.add(source)
        session.flush()
        for index, vector in enumerate([[0.1] * 768, None]):
            session.add(
                CodeChunk(
                    file_id=source.id,
                    repository_id=uuid.UUID(repository_id),
                    content=f"def f{index}(): pass",
                    kind=ChunkKind.function,
                    start_line=1,
                    end_line=1,
                    chunk_hash=f"{index}" * 64,
                    embedding=vector,
                    embedding_model="m" if vector else None,
                )
            )

    listed = client.get("/repositories").json()[0]
    assert listed["file_count"] == 1
    assert listed["chunk_count"] == 2
    # A partial embedding pass is reported as partial, not rounded up.
    assert listed["embedded_chunks"] == 1
