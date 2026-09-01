from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DependencyStatus = Literal["ok", "unavailable"]


class DependencyHealth(BaseModel):
    status: DependencyStatus
    latency_ms: float | None = None
    error: str | None = Field(
        default=None,
        description="Short failure reason. Never contains credentials or stack traces.",
    )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    environment: str
    dependencies: dict[str, DependencyHealth]
