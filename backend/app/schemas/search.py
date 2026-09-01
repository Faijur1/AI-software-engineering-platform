"""Request and response models for retrieval."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.rag.types import RetrievalMethod


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    # Bounded so one request cannot ask for the whole index.
    limit: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    """One retrieved chunk, with the evidence behind its ranking.

    The per-retriever scores and ranks are part of the response, not debug
    output: the RAG inspector exists so a retrieval failure can be diagnosed,
    and that is impossible from a single fused number.
    """

    chunk_id: uuid.UUID
    file_path: str
    symbol: str | None
    kind: str
    start_line: int
    end_line: int
    content: str

    method: RetrievalMethod
    fused_score: float
    vector_score: float | None = None
    vector_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    # Null while the reranker is a passthrough. Null means "not reranked",
    # never "reranked and unchanged".
    rerank_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    repository_id: uuid.UUID
    results: list[SearchHit]

    # How many candidates each retriever contributed, so a half-working hybrid
    # search is visible rather than silently degraded.
    vector_candidates: int
    keyword_candidates: int
    fused_candidates: int
    notes: list[str] = Field(default_factory=list)

    reranker: str
    # Surfaced so the UI can say the results are not reranked yet, rather than
    # implying a quality step that has not happened.
    reranker_is_passthrough: bool
