"""Request and response models for agent runs, traces and patches."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import AgentStatus, PatchStatus, ToolStatus


class StartAgentRequest(BaseModel):
    repository_id: uuid.UUID
    task: str = Field(min_length=1, max_length=2000)
    # Bounded in the schema, not only by a default: the cap is what stops a
    # confused model from looping indefinitely, so a client must not be able to
    # raise it arbitrarily.
    max_iterations: int = Field(default=8, ge=1, le=20)
    # Off by default. Running a repository's test suite is a much larger
    # capability than reading it, so it is granted per run and never assumed.
    allow_tests: bool = False


class ToolRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    iteration: int
    tool_name: str
    status: ToolStatus
    duration_ms: int
    input: dict[str, Any]
    output: dict[str, Any]
    error: str | None = None
    created_at: datetime


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: str
    repository_id: uuid.UUID
    task: str
    status: AgentStatus
    plan: str | None = None
    result: str | None = None
    iterations: int
    max_iterations: int
    model: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    created_at: datetime


class AgentRunDetail(AgentRunResponse):
    """A run with what it actually did.

    Rejected tool calls are included, not filtered out: they are how a run that
    went nowhere becomes diagnosable, and tool-selection accuracy is a metric.
    """

    tool_runs: list[ToolRunResponse] = Field(default_factory=list)
    patch_ids: list[uuid.UUID] = Field(default_factory=list)


class TraceEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    event_type: str
    component: str
    ts: datetime
    duration_ms: int | None = None
    status: str | None = None
    event_metadata: dict[str, Any] = Field(default_factory=dict)


class TraceResponse(BaseModel):
    trace_id: str
    events: list[TraceEvent]


class CreatePatchRequest(BaseModel):
    agent_run_id: uuid.UUID
    diff: str = Field(min_length=1, max_length=200_000)
    summary: str | None = Field(default=None, max_length=2000)
    # Whether to apply and test it in the sandbox before returning. Slow, so it
    # is a choice; the patch is stored either way with validated left null,
    # which means "not validated" and never "passed".
    validate_in_sandbox: bool = True


class PatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_run_id: uuid.UUID
    diff: str
    summary: str | None = None
    status: PatchStatus
    # Null means not validated. It must never be read as having passed.
    validated: bool | None = None
    validation_output: str | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime


class ApprovePatchRequest(BaseModel):
    # Explicit rather than inferred from the endpoint. Approving and rejecting
    # are both deliberate human acts and should read as such at the call site.
    approve: bool = True
