from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import Timestamps, UUIDPrimaryKey


class AgentStatus(StrEnum):
    """Terminal states are distinguishable on purpose.

    ``max_iterations_exceeded`` is not ``failed``: the run did real work and
    has partial state worth showing. Collapsing them would hide the difference
    between "this went wrong" and "this needed more room".
    """

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    max_iterations_exceeded = "max_iterations_exceeded"


class AgentRun(UUIDPrimaryKey, Timestamps, Base):
    """One bounded investigation of one task against one repository."""

    __tablename__ = "agent_runs"

    # Shared with every event, tool call and test run beneath it, so a whole
    # run reconstructs from one identifier.
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", native_enum=False, length=32),
        default=AgentStatus.queued,
        nullable=False,
        index=True,
    )
    # The model's stated approach, captured before it acts, so a bad run can be
    # read as "wrong plan" or as "right plan, wrong execution".
    plan: Mapped[str | None] = mapped_column(Text)
    # What the agent concluded. Null until it finishes.
    result: Mapped[str | None] = mapped_column(Text)

    iterations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    tool_runs: Mapped[list[ToolRun]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )
    test_runs: Mapped[list[TestRun]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )
    patches: Mapped[list[Patch]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )


class ToolStatus(StrEnum):
    succeeded = "succeeded"
    failed = "failed"
    # The model asked for something the registry does not contain, or asked
    # with arguments that failed validation. Recorded rather than discarded:
    # tool-selection accuracy is a metric (docs/agents.md).
    rejected = "rejected"


class ToolRun(UUIDPrimaryKey, Timestamps, Base):
    """One tool invocation, recorded whether or not it was allowed to run."""

    __tablename__ = "tool_runs"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ToolStatus] = mapped_column(
        Enum(ToolStatus, name="tool_status", native_enum=False, length=16), nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Genuinely open-shaped: every tool has its own schema (docs/database.md).
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    agent_run: Mapped[AgentRun] = relationship(back_populates="tool_runs")


class TestRun(UUIDPrimaryKey, Timestamps, Base):
    """One sandboxed execution of a test suite."""

    __tablename__ = "test_runs"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    command: Mapped[str] = mapped_column(Text, nullable=False)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False)
    timed_out: Mapped[bool] = mapped_column(default=False, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")

    agent_run: Mapped[AgentRun] = relationship(back_populates="test_runs")


class PatchStatus(StrEnum):
    """A patch is proposed until a human acts on it. There is no auto-apply."""

    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"


class Patch(UUIDPrimaryKey, Timestamps, Base):
    """A proposed change, as a unified diff. Never applied outside the sandbox."""

    __tablename__ = "patches"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    diff: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PatchStatus] = mapped_column(
        Enum(PatchStatus, name="patch_status", native_enum=False, length=16),
        default=PatchStatus.proposed,
        nullable=False,
        index=True,
    )
    # Whether the diff applied cleanly and the suite passed *inside the
    # sandbox*. Null means not validated -- never read it as validated.
    validated: Mapped[bool | None] = mapped_column()
    validation_output: Mapped[str | None] = mapped_column(Text)

    # Who approved, and when. An approval with no actor is not an approval.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_run: Mapped[AgentRun] = relationship(back_populates="patches")


class Event(UUIDPrimaryKey, Base):
    """One ordered step in a run.

    Append-only and never updated: the trace is a record of what happened, and
    the Stage 2 replay UI is a view over these rows rather than a
    reconstruction (docs/agents.md).
    """

    __tablename__ = "events"

    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Monotonic within a trace, so ordering never depends on timestamp
    # resolution when two events land in the same millisecond.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(32))
    # "metadata" is reserved on the declarative base, so the column is named
    # explicitly while the attribute avoids the clash.
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
