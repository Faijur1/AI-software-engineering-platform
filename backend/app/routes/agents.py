"""Agent runs and their traces.

Starting a run returns **202** with a record to poll, like indexing: a run is
minutes of model calls and sandboxed execution, so no HTTP request waits for
it.

Every read is scoped to the caller through the owning repository. A run id
alone is not enough to read someone else's investigation of their code.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.agent.engine import DEFAULT_MAX_ITERATIONS
from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, new_trace_id
from app.models.agent import AgentRun, Event
from app.models.chunk import CodeChunk
from app.models.repository import Repository
from app.queue import get_queue
from app.schemas.agent import (
    AgentRunDetail,
    AgentRunResponse,
    StartAgentRequest,
    ToolRunResponse,
    TraceEvent,
    TraceResponse,
)

router = APIRouter(tags=["agents"])
logger = get_logger(__name__)


def _owned_run(session: DbSession, run_id: uuid.UUID, user_id: uuid.UUID) -> AgentRun:
    """Load a run the caller owns, or report it as absent.

    Joined to repositories and filtered on the owner, so another user's run is
    indistinguishable from one that does not exist.
    """
    run = session.execute(
        select(AgentRun)
        .join(Repository, Repository.id == AgentRun.repository_id)
        .where(AgentRun.id == run_id, Repository.user_id == user_id)
        .options(selectinload(AgentRun.tool_runs), selectinload(AgentRun.patches))
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Agent run not found")
    return run


@router.post(
    "/agents/run",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an agent run",
)
def start_run(
    payload: StartAgentRequest, user: CurrentUser, session: DbSession
) -> AgentRun:
    """Queue an agent run and return the record to poll."""
    repository = session.execute(
        select(Repository).where(
            Repository.id == payload.repository_id, Repository.user_id == user.id
        )
    ).scalar_one_or_none()
    if repository is None:
        raise NotFoundError("Repository not found")

    embedded = session.execute(
        select(func.count())
        .select_from(CodeChunk)
        .where(
            CodeChunk.repository_id == repository.id,
            CodeChunk.embedding.is_not(None),
        )
    ).scalar_one()
    if embedded == 0:
        # The agent's first move is almost always a search. Starting a run
        # against an unindexed repository would burn iterations discovering
        # that there is nothing to find.
        raise ValidationError(
            "This repository has no embedded chunks yet. Index it first."
        )

    run = AgentRun(
        trace_id=new_trace_id(),
        repository_id=repository.id,
        task=payload.task,
        max_iterations=payload.max_iterations or DEFAULT_MAX_ITERATIONS,
    )
    session.add(run)
    session.flush()

    get_queue().enqueue_agent_run(run.id, allow_tests=payload.allow_tests)
    logger.info(
        "agent_run_queued",
        run_id=str(run.id),
        repository=repository.full_name,
        allow_tests=payload.allow_tests,
    )
    return run


@router.get(
    "/agents/runs/{run_id}",
    response_model=AgentRunDetail,
    summary="An agent run and what it did",
)
def get_run(run_id: uuid.UUID, user: CurrentUser, session: DbSession) -> AgentRunDetail:
    run = _owned_run(session, run_id, user.id)

    detail = AgentRunDetail.model_validate(run)
    # Ordered by iteration then creation, so the sequence reads as it happened.
    detail.tool_runs = [
        ToolRunResponse.model_validate(call)
        for call in sorted(run.tool_runs, key=lambda c: (c.iteration, c.created_at))
    ]
    detail.patch_ids = [patch.id for patch in run.patches]
    return detail


@router.get(
    "/traces/{trace_id}",
    response_model=TraceResponse,
    summary="Ordered events for one run",
)
def get_trace(trace_id: str, user: CurrentUser, session: DbSession) -> TraceResponse:
    """Return the recorded events for a trace.

    Ordered by sequence rather than timestamp: two events can land in the same
    millisecond, and an ordering that sometimes inverts is worse than none.
    """
    # Authorised through the run that owns the trace, so a guessed trace id
    # discloses nothing.
    owned = session.execute(
        select(AgentRun.trace_id)
        .join(Repository, Repository.id == AgentRun.repository_id)
        .where(AgentRun.trace_id == trace_id, Repository.user_id == user.id)
    ).scalar_one_or_none()
    if owned is None:
        raise NotFoundError("Trace not found")

    events = list(
        session.execute(
            select(Event).where(Event.trace_id == trace_id).order_by(Event.sequence)
        ).scalars()
    )
    return TraceResponse(
        trace_id=trace_id,
        events=[TraceEvent.model_validate(event) for event in events],
    )
