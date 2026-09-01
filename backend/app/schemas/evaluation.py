"""Response models for benchmark results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConfigurationScores(BaseModel):
    """Metrics for one retrieval configuration.

    Keyed by cutoff as a string, because JSON object keys are strings and
    pretending otherwise only moves the conversion somewhere less obvious.
    """

    recall: dict[str, float]
    precision: dict[str, float]
    hit_rate: dict[str, float]
    mrr: float
    # Per question style, so the claim that the two retrievers fail on
    # different kinds of question can be checked rather than assumed.
    by_style: dict[str, dict[str, float]] = Field(default_factory=dict)
    elapsed_seconds: float


class EvaluationReportResponse(BaseModel):
    """A completed benchmark run.

    Always a record of a real run against a real index, including the commit
    and chunk count it was measured on. There is no live-scoring path: numbers
    without a run behind them would be fabrication.
    """

    generated_at: str | None = None
    repository: str
    commit: str | None = None
    chunk_count: int
    question_count: int
    cutoffs: list[int]
    # Named so a consumer can tell whether reranking was active. While it is
    # "passthrough" these numbers are the pre-reranking baseline.
    reranker: str
    configurations: dict[str, Any]
