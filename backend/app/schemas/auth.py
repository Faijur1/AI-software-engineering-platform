"""Response models for the authentication endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    """The authenticated user, as the frontend is allowed to see them.

    Declared field-by-field rather than serialised from the ORM object, so that
    adding a sensitive column to ``User`` — a token, for example — cannot leak
    it through this endpoint by accident.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_id: int
    login: str
    name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
