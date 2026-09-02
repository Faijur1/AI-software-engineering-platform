"""Worker entrypoint for agent runs.

Owns the run lifecycle — status, timestamps, failure recording — and delegates
the investigation itself to the engine. As with indexing, every exit path
leaves the run in a terminal state: a run stuck at ``running`` forever is worse
than one marked failed, because the user can retry a failure and cannot
interpret a spinner.

The workspace is fetched once per run and torn down with it. The agent reads
files and may run tests against that snapshot, so it must be a snapshot of the
commit that was indexed, not whatever is on GitHub at the moment a tool happens
to fire.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.errors import AppError
from app.core.logging import bind_trace_id, get_logger
from app.core.security import decrypt_token
from app.models.agent import AgentRun, AgentStatus
from app.models.repository import Repository
from app.models.user import User

logger = get_logger(__name__)


def run_agent_job(run_id: str, allow_tests: bool = False) -> None:
    """Execute the agent run identified by ``run_id``.

    Called by RQ with primitives, since that is what survives serialisation.
    """
    identifier = uuid.UUID(run_id)

    try:
        _run(identifier, allow_tests=allow_tests)
    except Exception as exc:
        logger.exception("agent_run_failed", run_id=run_id)
        _record_failure(identifier, exc)
        raise


def _run(run_id: uuid.UUID, *, allow_tests: bool) -> None:
    # Imported here so the API process never pulls in the agent stack.
    from app.agent.engine import run_agent
    from app.agent.tools import Permission
    from app.ingestion.fetcher import fetch_snapshot

    with session_scope() as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            logger.warning("agent_run_missing", run_id=str(run_id))
            return

        bind_trace_id(run.trace_id)
        repository = session.get(Repository, run.repository_id)
        if repository is None:
            run.status = AgentStatus.failed
            run.error = "The repository was disconnected before the run started"
            run.finished_at = datetime.now(UTC)
            return

        user = session.get(User, repository.user_id)
        if user is None or not user.github_token_encrypted:
            run.status = AgentStatus.failed
            run.error = "No GitHub credentials are stored; sign in again"
            run.finished_at = datetime.now(UTC)
            return

        token = decrypt_token(user.github_token_encrypted)
        owner, name = repository.owner, repository.name
        # The commit that was indexed, so what the agent reads matches what it
        # can retrieve. Falling back to the branch head would let the two drift.
        ref = repository.current_commit or repository.default_branch
        repository_id = repository.id

        run.status = AgentStatus.running
        run.started_at = datetime.now(UTC)
        run.model = get_settings().llm_model

    granted = {Permission.repo_read}
    if allow_tests:
        granted.add(Permission.sandbox_execute)

    with fetch_snapshot(token, owner, name, ref) as snapshot, session_scope() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        outcome = run_agent(
            session,
            run,
            repository_id=repository_id,
            workspace=snapshot.root,
            granted=frozenset(granted),
        )

        run.status = outcome.status
        run.result = outcome.answer
        run.plan = outcome.plan
        run.iterations = outcome.iterations
        run.error = outcome.error
        run.finished_at = datetime.now(UTC)

    logger.info(
        "agent_run_complete",
        run_id=str(run_id),
        status=outcome.status.value,
        iterations=outcome.iterations,
    )


def _record_failure(run_id: uuid.UUID, exc: Exception) -> None:
    """Mark the run failed in a fresh transaction.

    Fresh because the transaction that raised has already rolled back; writing
    the failure through it would be rolled back too.
    """
    message = exc.message if isinstance(exc, AppError) else "The agent run failed unexpectedly"

    try:
        with session_scope() as session:
            run = session.get(AgentRun, run_id)
            if run is not None:
                run.status = AgentStatus.failed
                run.error = message
                run.finished_at = datetime.now(UTC)
    except Exception:
        logger.exception("agent_failure_not_recorded", run_id=str(run_id))
