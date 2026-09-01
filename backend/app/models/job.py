from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import Timestamps, UUIDPrimaryKey


class JobType(StrEnum):
    index_repository = "index_repository"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Job(UUIDPrimaryKey, Timestamps, Base):
    """A unit of background work, tracked in Postgres rather than in Redis.

    Redis holds the queue; this table holds the record. Keeping the record in
    Postgres is what makes a lost Redis job detectable rather than silent
    (ADR-003), and gives the UI something durable to poll.
    """

    __tablename__ = "jobs"

    type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type", native_enum=False, length=32), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False, length=16),
        default=JobStatus.queued,
        nullable=False,
        index=True,
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    # 0-100. Coarse on purpose: it exists to show the user that work is
    # advancing, not to be a precise completion estimate.
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Short human-readable stage, e.g. "parsing files".
    stage: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failure reason, safe to show the user. Stack traces stay in the logs.
    error: Mapped[str | None] = mapped_column(Text)
