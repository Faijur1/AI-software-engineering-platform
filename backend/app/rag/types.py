"""Shared result types for retrieval.

Every candidate carries how it was found and what each retriever scored it,
not just a final number. That is a requirement, not a convenience: the RAG
inspector (milestone 8) exists so a retrieval failure can be diagnosed, and
"why was this ranked here" is unanswerable from a fused score alone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class RetrievalMethod(StrEnum):
    """Which retriever(s) surfaced a chunk."""

    vector = "vector"
    keyword = "keyword"
    both = "both"


@dataclass(slots=True)
class Candidate:
    """One chunk returned by a retriever, before fusion."""

    chunk_id: uuid.UUID
    file_path: str
    symbol: str | None
    kind: str
    start_line: int
    end_line: int
    content: str
    # Raw, retriever-specific score. Cosine similarity for the vector side,
    # ts_rank for the keyword side. The two are not comparable -- see
    # ADR-011 for why fusion is rank-based rather than score-based.
    score: float
    # 1-based position within that retriever's own result list.
    rank: int


@dataclass(slots=True)
class RetrievedChunk:
    """A candidate after fusion, with everything the inspector needs."""

    chunk_id: uuid.UUID
    file_path: str
    symbol: str | None
    kind: str
    start_line: int
    end_line: int
    content: str

    method: RetrievalMethod
    # Fused rank score. Comparable only within one query's result set.
    fused_score: float
    # Raw scores and ranks from each retriever, kept for the inspector.
    vector_score: float | None = None
    vector_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    # Set by the reranker. None while the passthrough reranker is in use, so a
    # missing value is visibly missing rather than silently equal to the fused
    # score (milestone 7).
    rerank_score: float | None = None
    selected: bool = False


@dataclass(slots=True)
class RetrievalTrace:
    """What a single retrieval actually did. Counted, never estimated."""

    query: str
    repository_id: uuid.UUID
    vector_candidates: int = 0
    keyword_candidates: int = 0
    fused_candidates: int = 0
    returned: int = 0
    # Populated when the keyword query reduces to nothing -- for example a
    # query made entirely of stopwords -- so a half-working hybrid search is
    # visible rather than silently vector-only.
    notes: list[str] = field(default_factory=list)
