"""Response models for repository listing and connection."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.repository import IndexStatus


class GitHubRepositoryResponse(BaseModel):
    """A repository on GitHub, with whether it is connected here."""

    github_id: int
    owner: str
    name: str
    full_name: str
    description: str | None = None
    default_branch: str
    is_private: bool
    language: str | None = None
    updated_at: str | None = None
    html_url: str

    # Set from the local database, not from GitHub: it tells the UI whether to
    # offer "Connect" or link to the already-connected repository.
    connected_id: uuid.UUID | None = None


class GitHubRepositoryPage(BaseModel):
    """One page of GitHub repositories.

    GitHub does not report a total count on this endpoint, so pagination is
    expressed as ``has_next`` rather than a total — inventing a total would be
    a fabricated number.
    """

    items: list[GitHubRepositoryResponse]
    page: int
    per_page: int
    has_next: bool


class ConnectRepositoryRequest(BaseModel):
    """Connect a repository the caller can already see on GitHub.

    Identified by owner/name rather than by GitHub ID: the server re-fetches it
    with the caller's own token, which is what proves they may connect it. A
    client-supplied ID would be trusted input.
    """

    owner: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)


class RepositoryResponse(BaseModel):
    """A repository connected to this platform."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_id: int
    owner: str
    name: str
    default_branch: str
    is_private: bool
    index_status: IndexStatus
    indexed_at: datetime | None = None
    created_at: datetime
