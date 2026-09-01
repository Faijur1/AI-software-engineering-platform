"""Response models for background jobs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.job import JobStatus, JobType


class JobResponse(BaseModel):
    """A background job as the client may see it.

    ``error`` carries only messages already judged safe to show a user; the
    worker never copies an unexpected exception into it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    status: JobStatus
    repository_id: uuid.UUID
    progress: int
    stage: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    created_at: datetime
