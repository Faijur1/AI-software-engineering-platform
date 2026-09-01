"""Reranking, behind an interface.

The real implementation is a cross-encoder (``bge-reranker-base``) and arrives
in **milestone 7**. What ships now is a passthrough that changes nothing.

That is deliberate, and the ordering matters: milestone 6 builds a labelled
benchmark and measures Recall@K and Precision@K *before* the reranker exists.
Without a baseline measured against a genuinely inert reranker, the reranker's
contribution could only be asserted, never demonstrated.

The passthrough is therefore honest about being one. It leaves ``rerank_score``
as ``None`` rather than copying the fused score across, so nothing downstream
can mistake "not reranked" for "reranked and unchanged", and the inspector
shows a blank column instead of a fabricated number.
"""

from __future__ import annotations

from typing import Protocol

from app.rag.types import RetrievedChunk


class Reranker(Protocol):
    """Narrows a wide candidate set to the chunks actually worth sending."""

    @property
    def name(self) -> str:
        """Identifier shown in the inspector, so the active strategy is visible."""
        ...

    @property
    def is_passthrough(self) -> bool:
        """Whether this reranker actually reorders anything.

        Part of the interface so the API can report it. A UI that cannot
        distinguish a real reranker from a placeholder invites exactly the kind
        of unearned confidence this project is trying to avoid.
        """
        ...

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        """Return at most ``limit`` chunks, best first."""
        ...


class PassthroughReranker:
    """Truncates the fused list. Does not reorder, and does not claim to."""

    @property
    def name(self) -> str:
        return "passthrough"

    @property
    def is_passthrough(self) -> bool:
        return True

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        selected = candidates[:limit]
        for chunk in selected:
            chunk.selected = True
            # rerank_score is deliberately left as None. See the module
            # docstring: an absent score must stay visibly absent.
        return selected
