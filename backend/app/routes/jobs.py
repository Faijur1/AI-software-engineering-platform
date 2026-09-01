"""Job status.

Long-running work returns a job the client polls, so no HTTP request ever
blocks on indexing (docs/api.md).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.models.job import Job
from app.models.repository import Repository
from app.schemas.job import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse, summary="Job status")
def get_job(job_id: uuid.UUID, user: CurrentUser, session: DbSession) -> Job:
    """Return one job belonging to the caller.

    Joined to ``repositories`` and filtered on the owner: a job id alone must
    not be enough to read another user's indexing progress. As elsewhere, an
    inaccessible job is reported as absent rather than forbidden.
    """
    job = session.execute(
        select(Job)
        .join(Repository, Repository.id == Job.repository_id)
        .where(Job.id == job_id, Repository.user_id == user.id)
    ).scalar_one_or_none()

    if job is None:
        raise NotFoundError("Job not found")
    return job
