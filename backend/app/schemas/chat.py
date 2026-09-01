"""Request and response models for chat."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    repository_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)
    # How many retrieved chunks to put in front of the model. Bounded: a larger
    # context is not automatically a better one, and the budget is finite.
    max_sources: int = Field(default=8, ge=1, le=20)


class CitedSource(BaseModel):
    """One source the answer could cite, numbered as the model saw it."""

    index: int
    chunk_id: uuid.UUID
    file_path: str
    symbol: str | None = None
    start_line: int
    end_line: int
    content: str
    # Whether the answer actually referred to this source.
    cited: bool = False


class CitationCheck(BaseModel):
    """Mechanical verification of the answer's citations.

    Deliberately not a groundedness score. Whether each claim is supported by
    the source it cites needs a judge, and an unvalidated judge would be a
    number with nothing behind it.
    """

    valid: bool
    invalid_indices: list[int] = Field(default_factory=list)
    # Share of sentences carrying a citation. Coverage, not correctness.
    citation_coverage: float


class ChatResponse(BaseModel):
    question: str
    repository_id: uuid.UUID
    answer: str
    sources: list[CitedSource]
    citations: CitationCheck

    model: str
    reranker: str
    # Counted, so a half-filled context is visible rather than silent.
    retrieved_candidates: int
    sources_offered: int
    sources_included: int
    estimated_context_tokens: int
    duration_ms: int
    notes: list[str] = Field(default_factory=list)
