"""Hybrid retrieval: run both retrievers, fuse, rerank.

```
query -> embed ------> vector search --\\
      -> tsquery ----> keyword search --> RRF fuse -> dedupe -> rerank -> top K
```

The two retrievers fail in opposite directions, which is the entire reason for
running both (docs/rag.md). Vector search handles "how is payment retried?" and
is unreliable on exact identifiers; keyword search is exact on identifiers and
fails when the user's words differ from the code's.

A wide candidate set is retrieved (~50) and narrowed to the handful actually
used. That split is what makes the reranker in milestone 7 a drop-in change
rather than a restructuring.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.base import EmbeddingProvider
from app.rag import keyword, vector
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.reranker import PassthroughReranker, Reranker
from app.rag.types import RetrievalTrace, RetrievedChunk

logger = get_logger(__name__)

# Retrieve wide, return narrow. 50 is enough for the reranker to have something
# to work with without making its job (milestone 7) expensive.
DEFAULT_CANDIDATE_LIMIT: Final = 50
DEFAULT_RESULT_LIMIT: Final = 10


@dataclass(slots=True)
class RetrievalResult:
    """The chunks selected, plus what happened on the way there."""

    chunks: list[RetrievedChunk]
    trace: RetrievalTrace
    reranker: str
    reranker_is_passthrough: bool


def retrieve(
    session: Session,
    embedder: EmbeddingProvider,
    *,
    repository_id: uuid.UUID,
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    reranker: Reranker | None = None,
    use_vector: bool = True,
    use_keyword: bool = True,
) -> RetrievalResult:
    """Retrieve chunks for ``query`` within one repository.

    ``use_vector`` and ``use_keyword`` disable one half. They exist so the
    evaluation harness can measure each retriever alone against the same
    benchmark -- a hybrid claim is worth nothing without the two baselines it
    is being compared to.
    """
    active_reranker = reranker or PassthroughReranker()
    trace = RetrievalTrace(query=query, repository_id=repository_id)

    vector_results = []
    if not use_vector:
        trace.notes.append("Vector search was disabled for this query.")
    else:
        try:
            query_vector = embedder.embed([query])[0]
            vector_results = vector.search(
                session,
                repository_id=repository_id,
                query_vector=query_vector,
                limit=candidate_limit,
            )
        except Exception as exc:
            # A failed embedding must not take the whole search down: keyword
            # search alone still returns useful results, and the degradation is
            # recorded rather than hidden.
            logger.warning("vector_retrieval_failed", error=type(exc).__name__)
            trace.notes.append(
                "Vector search was unavailable; these results are keyword-only."
            )

    keyword_results = []
    if not use_keyword:
        trace.notes.append("Keyword search was disabled for this query.")
    elif keyword.build_query(session, query) is None:
        # Nothing searchable survived stopword removal.
        trace.notes.append(
            "The query contained no searchable terms for keyword search; "
            "these results are vector-only."
        )
    else:
        keyword_results = keyword.search(
            session,
            repository_id=repository_id,
            query_text=query,
            limit=candidate_limit,
        )

    trace.vector_candidates = len(vector_results)
    trace.keyword_candidates = len(keyword_results)

    fused = reciprocal_rank_fusion(vector_results, keyword_results)
    trace.fused_candidates = len(fused)

    selected = active_reranker.rerank(query, fused, limit=limit)
    trace.returned = len(selected)

    logger.info(
        "retrieval_complete",
        repository_id=str(repository_id),
        vector=trace.vector_candidates,
        keyword=trace.keyword_candidates,
        fused=trace.fused_candidates,
        returned=trace.returned,
    )

    return RetrievalResult(
        chunks=selected,
        trace=trace,
        reranker=active_reranker.name,
        reranker_is_passthrough=active_reranker.is_passthrough,
    )
