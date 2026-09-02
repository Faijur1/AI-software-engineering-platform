"""Repository listing and connection.

``GET /repositories/github`` is a live view of what the caller can see on
GitHub; ``GET /repositories`` is what they have connected to this platform.
Keeping them separate keeps the local database from silently becoming a stale
mirror of GitHub.

Every query is scoped to the authenticated user, and connecting a repository is
authorised by re-fetching it with the caller's own token — never by trusting an
identifier supplied by the client.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession, GitHubToken
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models.chunk import CodeChunk
from app.models.file import File
from app.models.job import Job, JobStatus, JobType
from app.models.repository import IndexStatus, Repository
from app.queue import get_queue
from app.schemas.job import JobResponse
from app.schemas.repository import (
    ConnectRepositoryRequest,
    GitHubRepositoryPage,
    GitHubRepositoryResponse,
    RepositoryResponse,
    RepositorySettingsRequest,
)
from app.services import github

router = APIRouter(prefix="/repositories", tags=["repositories"])
logger = get_logger(__name__)


@router.get("/github", response_model=GitHubRepositoryPage, summary="Repositories on GitHub")
def list_github_repositories(
    user: CurrentUser,
    session: DbSession,
    token: GitHubToken,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=github.MAX_PER_PAGE),
) -> GitHubRepositoryPage:
    """List the caller's GitHub repositories, marking the connected ones.

    One extra item beyond the page is requested so ``has_next`` reflects a real
    observation rather than a guess from a full page.
    """
    fetched = github.list_repositories(token, page=page, per_page=per_page + 1)
    has_next = len(fetched) > per_page
    items = fetched[:per_page]

    connected = {
        github_id: repo_id
        for github_id, repo_id in session.execute(
            select(Repository.github_id, Repository.id).where(Repository.user_id == user.id)
        ).all()
    }

    return GitHubRepositoryPage(
        items=[
            GitHubRepositoryResponse(
                github_id=repo.id,
                owner=repo.owner,
                name=repo.name,
                full_name=repo.full_name,
                description=repo.description,
                default_branch=repo.default_branch,
                is_private=repo.is_private,
                language=repo.language,
                updated_at=repo.updated_at,
                html_url=repo.html_url,
                connected_id=connected.get(repo.id),
            )
            for repo in items
        ],
        page=page,
        per_page=per_page,
        has_next=has_next,
    )


@router.get("", response_model=list[RepositoryResponse], summary="Connected repositories")
def list_connected_repositories(
    user: CurrentUser, session: DbSession
) -> list[RepositoryResponse]:
    """List connected repositories with their real index counts.

    Counts come from two grouped aggregates rather than a per-row query, so the
    endpoint stays at three queries however many repositories are connected.
    """
    repositories = list(
        session.execute(
            select(Repository)
            .where(Repository.user_id == user.id)
            .order_by(Repository.created_at.desc())
        ).scalars()
    )
    if not repositories:
        return []

    ids = [repo.id for repo in repositories]

    files: dict[uuid.UUID, int] = {
        repository_id: count
        for repository_id, count in session.execute(
            select(File.repository_id, func.count())
            .where(File.repository_id.in_(ids))
            .group_by(File.repository_id)
        ).all()
    }
    chunks: dict[uuid.UUID, tuple[int, int]] = {
        repository_id: (total, embedded)
        for repository_id, total, embedded in session.execute(
            select(
                CodeChunk.repository_id,
                func.count(),
                func.count(CodeChunk.embedding),
            )
            .where(CodeChunk.repository_id.in_(ids))
            .group_by(CodeChunk.repository_id)
        ).all()
    }

    responses: list[RepositoryResponse] = []
    for repo in repositories:
        total, embedded = chunks.get(repo.id, (0, 0))
        response = RepositoryResponse.model_validate(repo)
        response.file_count = files.get(repo.id, 0)
        response.chunk_count = total
        response.embedded_chunks = embedded
        responses.append(response)
    return responses


@router.post(
    "",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a repository",
)
def connect_repository(
    payload: ConnectRepositoryRequest,
    user: CurrentUser,
    session: DbSession,
    token: GitHubToken,
) -> Repository:
    """Connect a GitHub repository to the caller's account.

    The repository is re-fetched from GitHub with the caller's token: if they
    cannot see it there, GitHub returns 404 and so does this endpoint. That is
    the authorisation check — access is never inferred from the request body.
    """
    remote = github.get_repository(token, payload.owner, payload.name)

    existing = session.execute(
        select(Repository).where(
            Repository.user_id == user.id, Repository.github_id == remote.id
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Idempotent: reconnecting refreshes the metadata rather than failing,
        # since a rename or a default-branch change is a normal occurrence.
        existing.owner = remote.owner
        existing.name = remote.name
        existing.default_branch = remote.default_branch
        existing.is_private = remote.is_private
        session.flush()
        return existing

    repository = Repository(
        user_id=user.id,
        github_id=remote.id,
        owner=remote.owner,
        name=remote.name,
        default_branch=remote.default_branch,
        is_private=remote.is_private,
    )
    session.add(repository)
    session.flush()
    logger.info("repository_connected", user_id=str(user.id), repository=remote.full_name)
    return repository


@router.post(
    "/{repository_id}/index",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue indexing for a repository",
)
def index_repository(
    repository_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
) -> Job:
    """Queue an indexing run and return the job to poll.

    202, never 200: the work has been accepted, not performed. Indexing takes
    minutes, so no HTTP request waits for it (docs/api.md).
    """
    repository = _owned_repository(session, repository_id, user.id)

    # Re-queueing a repository that is already being indexed would have two
    # workers writing the same chunks. The existing job is returned instead, so
    # a double-click is harmless and the client still gets something to poll.
    active = session.execute(
        select(Job)
        .where(
            Job.repository_id == repository.id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        )
        .order_by(Job.created_at.desc())
    ).scalars().first()
    if active is not None:
        return active

    job = Job(type=JobType.index_repository, repository_id=repository.id)
    session.add(job)
    repository.index_status = IndexStatus.queued
    # Flushed so the row exists before the worker can possibly pick it up.
    session.flush()

    # Enqueued after the flush but inside the transaction: if the commit fails,
    # the worker finds no job row and exits cleanly, which is the safe way for
    # this race to resolve. The reverse order could hand the worker an id that
    # never becomes a row.
    get_queue().enqueue_index_repository(job.id)

    logger.info(
        "index_queued",
        user_id=str(user.id),
        repository=repository.full_name,
        job_id=str(job.id),
    )
    return job


def _owned_repository(
    session: DbSession, repository_id: uuid.UUID, user_id: uuid.UUID
) -> Repository:
    """Load a repository the caller owns, or report it as absent.

    Filtered on user_id as well as the primary key, so another user's
    repository is indistinguishable from one that does not exist.
    """
    repository = session.execute(
        select(Repository).where(
            Repository.id == repository_id, Repository.user_id == user_id
        )
    ).scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found")
    return repository


@router.delete(
    "/{repository_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a repository",
)
def disconnect_repository(repository_id: uuid.UUID, user: CurrentUser, session: DbSession) -> None:
    """Disconnect a repository.

    Filtered on ``user_id`` as well as the primary key, so another user's
    repository is indistinguishable from one that does not exist (404, not 403).
    """
    session.delete(_owned_repository(session, repository_id, user.id))


@router.patch(
    "/{repository_id}/settings",
    response_model=RepositoryResponse,
    summary="Change a repository's settings",
)
def update_repository_settings(
    repository_id: uuid.UUID,
    payload: RepositorySettingsRequest,
    user: CurrentUser,
    session: DbSession,
) -> RepositoryResponse:
    """Grant or withdraw permission to send this repository to a cloud model.

    Owner-scoped through ``_owned_repository``, so another user's repository is
    reported as absent rather than forbidden.

    The grant is explicit and per repository. Enabling a cloud provider in
    configuration does not opt any repository in, and connecting a repository
    later does not inherit anyone's earlier decision -- answering a question
    sends retrieved source code to whoever generates the answer, which is a
    disclosure about *this* repository.

    Withdrawal takes effect on the next question. It cannot recall what was
    already sent, and the response says so rather than implying otherwise.
    """
    repository = _owned_repository(session, repository_id, user.id)

    was_allowed = repository.allow_cloud_llm
    repository.allow_cloud_llm = payload.allow_cloud_llm
    if payload.allow_cloud_llm:
        # Refreshed only on a real transition, so the timestamp answers "when
        # was this granted" rather than "when was it last confirmed".
        if not was_allowed:
            repository.cloud_llm_allowed_at = datetime.now(UTC)
    else:
        repository.cloud_llm_allowed_at = None
    session.flush()

    if was_allowed != payload.allow_cloud_llm:
        logger.info(
            "repository_cloud_permission_changed",
            repository=repository.full_name,
            allow_cloud_llm=payload.allow_cloud_llm,
        )

    # Counts included so the client can render the row without a second call.
    total, embedded = session.execute(
        select(func.count(), func.count(CodeChunk.embedding)).where(
            CodeChunk.repository_id == repository.id
        )
    ).one()
    response = RepositoryResponse.model_validate(repository)
    response.file_count = session.execute(
        select(func.count()).select_from(File).where(File.repository_id == repository.id)
    ).scalar_one()
    response.chunk_count = total
    response.embedded_chunks = embedded
    return response
