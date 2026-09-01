"""Worker entrypoint for repository indexing.

Owns the job lifecycle -- status, progress, timestamps, failure recording --
and delegates the actual work to the ingestion service. Splitting it this way
means the service can be tested without a queue, and this module's job is only
to make sure a job row always reaches a terminal state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import AppError
from app.core.logging import bind_trace_id, get_logger, new_trace_id
from app.core.security import decrypt_token
from app.models.job import Job, JobStatus
from app.models.repository import IndexStatus, Repository
from app.models.user import User

logger = get_logger(__name__)


def run_index_job(job_id: str) -> None:
    """Execute the indexing job identified by ``job_id``.

    Called by RQ with a string, since that is what survives serialisation.

    Every exit path leaves the job in a terminal state. A job stuck at
    ``running`` forever is worse than a job marked failed: the user can retry a
    failure, but has no way to interpret a spinner that never stops.
    """
    bind_trace_id(new_trace_id())
    identifier = uuid.UUID(job_id)

    try:
        _run(identifier)
    except Exception as exc:
        logger.exception("index_job_failed", job_id=job_id, error=str(exc))
        _record_failure(identifier, exc)
        # Re-raised so RQ also records the failure; the database row is already
        # correct either way.
        raise


def _run(job_id: uuid.UUID) -> None:
    # Imported here rather than at module scope so that importing this module
    # -- which the API does not do -- is what pulls in tree-sitter.
    from app.ingestion.embedder import embed_repository
    from app.ingestion.service import index_repository
    from app.llm.ollama import OllamaEmbedder

    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.warning("index_job_missing", job_id=str(job_id))
            return

        repository = session.get(Repository, job.repository_id)
        if repository is None:
            # The repository was disconnected between enqueue and execution.
            job.status = JobStatus.failed
            job.error = "The repository was disconnected before indexing started"
            job.finished_at = datetime.now(UTC)
            return

        user = session.get(User, repository.user_id)
        if user is None or not user.github_token_encrypted:
            job.status = JobStatus.failed
            job.error = "No GitHub credentials are stored; sign in again"
            job.finished_at = datetime.now(UTC)
            repository.index_status = IndexStatus.failed
            return

        token = decrypt_token(user.github_token_encrypted)
        owner, name, ref = repository.owner, repository.name, repository.default_branch
        repository_id = repository.id

        job.status = JobStatus.running
        job.started_at = datetime.now(UTC)
        job.stage = "starting"
        repository.index_status = IndexStatus.indexing

    def set_progress(percent: int, stage: str) -> None:
        # A separate short transaction per update, so progress is visible to
        # the polling UI while the long transaction below is still open.
        with session_scope() as progress_session:
            row = progress_session.get(Job, job_id)
            if row is not None:
                row.progress = percent
                row.stage = stage

    embedder = OllamaEmbedder()
    # Checked before any parsing: a missing model should fail in seconds with
    # an actionable message, not after minutes of work.
    embedder.check_available()

    # Parsing and embedding are separate transactions on purpose. If embedding
    # fails, the parsed chunks stay committed and a re-run embeds only what is
    # still missing, rather than re-parsing the whole repository.
    with session_scope() as session:
        result = index_repository(
            session,
            repository_id=repository_id,
            owner=owner,
            name=name,
            ref=ref,
            token=token,
            on_progress=lambda pct, stage: set_progress(_parse_share(pct), stage),
        )
        commit_sha = result.commit_sha

    with session_scope() as session:
        embed_repository(
            session,
            embedder,
            repository_id=repository_id,
            batch_size=get_settings().embedding_batch_size,
            on_progress=lambda done, total: set_progress(
                _embed_share(done, total), f"embedding ({done}/{total})"
            ),
        )

    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.succeeded
            job.progress = 100
            job.stage = "complete"
            job.finished_at = datetime.now(UTC)

        repository = session.get(Repository, repository_id)
        if repository is not None:
            repository.index_status = IndexStatus.indexed
            repository.current_commit = commit_sha
            repository.indexed_at = datetime.now(UTC)


def _record_failure(job_id: uuid.UUID, exc: Exception) -> None:
    """Mark the job and its repository failed, in a fresh transaction.

    Fresh because the transaction that raised has already been rolled back;
    writing the failure through it would be rolled back too.
    """
    # Only messages from AppError are safe to show a user. Anything else could
    # embed a DSN or a path, so it stays in the logs and the user sees a
    # generic message.
    message = exc.message if isinstance(exc, AppError) else "Indexing failed unexpectedly"

    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.failed
                job.error = message
                job.finished_at = datetime.now(UTC)
                repository = session.get(Repository, job.repository_id)
                if repository is not None:
                    repository.index_status = IndexStatus.failed
    except Exception:
        # The database is unreachable. Nothing more can be done here, and
        # masking the original failure would be worse.
        logger.exception("index_job_failure_not_recorded", job_id=str(job_id))


# Parsing occupies the first 60% of the reported progress and embedding the
# rest. Rough on purpose: embedding dominates wall-clock time on a first index
# but is skipped almost entirely on a re-index, so no fixed split is accurate
# for both. It shows that work is advancing, which is all it claims to do.
_PARSE_SHARE = 60


def _parse_share(percent: int) -> int:
    return int(percent * _PARSE_SHARE / 100)


def _embed_share(done: int, total: int) -> int:
    if total <= 0:
        return _PARSE_SHARE
    return _PARSE_SHARE + int((100 - _PARSE_SHARE) * done / total)
