"""Merge two ranked result sets into one.

Reciprocal Rank Fusion (ADR-011): each result contributes ``1 / (k + rank)``
from every list it appears in, and the contributions are summed.

Two properties follow directly, and both are what the design asks for
(docs/rag.md):

*Scores never have to be comparable.* Cosine similarity lives in roughly
[0, 1]; ``ts_rank`` is unbounded and depends on document length. Only their
**ranks** are used, so nothing has to be rescaled between two distributions
that have no common meaning.

*A chunk found by both retrievers outranks one found by either alone.* It
receives two contributions rather than one, so agreement between independent
retrievers is rewarded without any hand-tuned weight.
"""

from __future__ import annotations

from app.rag.types import Candidate, RetrievalMethod, RetrievedChunk

# The RRF smoothing constant from Cormack et al. (2009). Its effect is to stop
# rank 1 from dominating: with k=60 the gap between ranks 1 and 2 is small
# relative to the gain from appearing in both lists, which is exactly the
# behaviour hybrid search wants. Left at the published default rather than
# tuned, because tuning it without the milestone-6 benchmark would be guessing.
RRF_K = 60


def reciprocal_rank_fusion(
    vector_results: list[Candidate],
    keyword_results: list[Candidate],
) -> list[RetrievedChunk]:
    """Fuse two ranked lists, deduplicating by chunk id.

    Deduplication is by chunk id, keeping the evidence from both retrievers so
    the inspector can show why something ranked where it did.
    """
    fused: dict[str, RetrievedChunk] = {}

    for candidate in vector_results:
        key = str(candidate.chunk_id)
        fused[key] = _to_chunk(candidate, RetrievalMethod.vector)
        fused[key].vector_score = candidate.score
        fused[key].vector_rank = candidate.rank
        fused[key].fused_score = _contribution(candidate.rank)

    for candidate in keyword_results:
        key = str(candidate.chunk_id)
        existing = fused.get(key)
        if existing is None:
            entry = _to_chunk(candidate, RetrievalMethod.keyword)
            entry.keyword_score = candidate.score
            entry.keyword_rank = candidate.rank
            entry.fused_score = _contribution(candidate.rank)
            fused[key] = entry
            continue

        # Found by both: keep each retriever's evidence and add the second
        # contribution, which is what lifts agreed-upon results.
        existing.method = RetrievalMethod.both
        existing.keyword_score = candidate.score
        existing.keyword_rank = candidate.rank
        existing.fused_score += _contribution(candidate.rank)

    # Sorted by fused score, then by chunk id so the order is deterministic
    # when scores tie -- an unstable order would make evaluation runs in
    # milestone 6 irreproducible.
    return sorted(
        fused.values(),
        key=lambda chunk: (-chunk.fused_score, str(chunk.chunk_id)),
    )


def _contribution(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def _to_chunk(candidate: Candidate, method: RetrievalMethod) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=candidate.chunk_id,
        file_path=candidate.file_path,
        symbol=candidate.symbol,
        kind=candidate.kind,
        start_line=candidate.start_line,
        end_line=candidate.end_line,
        content=candidate.content,
        method=method,
        fused_score=0.0,
    )
