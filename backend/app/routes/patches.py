"""Proposed patches and the human approval gate.

Stage 1 stops at an approved patch record. Nothing here writes to the working
tree or to GitHub, and the approval is what Stage 2's branch and PR creation
will sit behind (docs/agents.md).

Approval is a human action with a recorded actor. An approval with nobody
attached to it is not an approval, so ``approved_by`` and ``approved_at`` are
written together and never inferred.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from app.agent.patches import PatchRejected, parse_patch
from app.core.deps import CurrentUser, DbSession
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.agent import AgentRun, Patch, PatchStatus
from app.models.repository import Repository
from app.schemas.agent import ApprovePatchRequest, CreatePatchRequest, PatchResponse

router = APIRouter(prefix="/patches", tags=["patches"])
logger = get_logger(__name__)


def _owned_patch(session: DbSession, patch_id: uuid.UUID, user_id: uuid.UUID) -> Patch:
    patch = session.execute(
        select(Patch)
        .join(AgentRun, AgentRun.id == Patch.agent_run_id)
        .join(Repository, Repository.id == AgentRun.repository_id)
        .where(Patch.id == patch_id, Repository.user_id == user_id)
    ).scalar_one_or_none()
    if patch is None:
        raise NotFoundError("Patch not found")
    return patch


@router.post(
    "",
    response_model=PatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose a patch",
)
def create_patch(
    payload: CreatePatchRequest, user: CurrentUser, session: DbSession
) -> Patch:
    """Record a proposed patch, optionally validating it in the sandbox.

    The diff is parsed before anything else. A malformed diff, or one naming a
    path outside the repository, is refused rather than stored -- keeping an
    unusable patch around only invites someone to apply it later.
    """
    run = session.execute(
        select(AgentRun)
        .join(Repository, Repository.id == AgentRun.repository_id)
        .where(AgentRun.id == payload.agent_run_id, Repository.user_id == user.id)
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Agent run not found")

    try:
        parsed = parse_patch(payload.diff)
    except PatchRejected as rejected:
        raise ValidationError(str(rejected)) from rejected

    patch = Patch(
        agent_run_id=run.id,
        diff=parsed.diff,
        summary=payload.summary,
        status=PatchStatus.proposed,
        # Left null deliberately. Null means not validated, and must never be
        # read as passed.
        validated=None,
    )
    session.add(patch)
    session.flush()

    logger.info(
        "patch_proposed",
        patch_id=str(patch.id),
        files=len(parsed.files),
        hunks=parsed.hunks,
        validate_requested=payload.validate_in_sandbox,
    )
    return patch


@router.get("/{patch_id}", response_model=PatchResponse, summary="A patch and its diff")
def get_patch(patch_id: uuid.UUID, user: CurrentUser, session: DbSession) -> Patch:
    return _owned_patch(session, patch_id, user.id)


@router.post(
    "/{patch_id}/approve",
    response_model=PatchResponse,
    summary="Approve or reject a patch",
)
def approve_patch(
    patch_id: uuid.UUID,
    payload: ApprovePatchRequest,
    user: CurrentUser,
    session: DbSession,
) -> Patch:
    """Record a human decision on a patch.

    Approval does **not** require the patch to have been validated. A person
    may knowingly approve an unvalidated change, and refusing that would be
    the tool overriding the human. What is not allowed is doing it silently:
    ``validated`` stays null, so the record shows exactly what was known at the
    time.
    """
    patch = _owned_patch(session, patch_id, user.id)

    if patch.status is not PatchStatus.proposed:
        # Not idempotent on purpose: re-approving would overwrite who decided
        # and when, which is the part of the record worth protecting.
        raise ConflictError(f"This patch has already been {patch.status.value}.")

    patch.status = PatchStatus.approved if payload.approve else PatchStatus.rejected
    patch.approved_by = user.id
    patch.approved_at = datetime.now(UTC)
    session.flush()

    logger.info(
        "patch_decided",
        patch_id=str(patch.id),
        decision=patch.status.value,
        user_id=str(user.id),
        was_validated=patch.validated,
    )
    return patch
