"""The passthrough reranker.

Its whole job is to be inert and to say so. Tested because "the placeholder
quietly started looking like a real result" is exactly the failure this
project's principles rule out.
"""

from __future__ import annotations

import uuid

from app.rag.reranker import PassthroughReranker
from app.rag.types import RetrievalMethod, RetrievedChunk


def _chunk(index: int, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(int=index),
        file_path=f"a{index}.py",
        symbol=None,
        kind="function",
        start_line=1,
        end_line=2,
        content="x",
        method=RetrievalMethod.both,
        fused_score=score,
    )


def test_it_declares_itself_a_passthrough() -> None:
    reranker = PassthroughReranker()

    assert reranker.is_passthrough is True
    assert reranker.name == "passthrough"


def test_it_does_not_reorder() -> None:
    candidates = [_chunk(0, 0.9), _chunk(1, 0.5), _chunk(2, 0.1)]

    result = PassthroughReranker().rerank("q", candidates, limit=3)

    assert [c.chunk_id for c in result] == [c.chunk_id for c in candidates]


def test_it_truncates_to_the_limit() -> None:
    candidates = [_chunk(i, 1.0 - i / 10) for i in range(10)]

    result = PassthroughReranker().rerank("q", candidates, limit=3)

    assert len(result) == 3


def test_it_leaves_rerank_score_absent() -> None:
    """Copying the fused score across would make "not reranked" indistinguishable
    from "reranked and unchanged"."""
    result = PassthroughReranker().rerank("q", [_chunk(0, 0.9)], limit=1)

    assert result[0].rerank_score is None
    assert result[0].selected is True
