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

from typing import Final, Protocol

from app.rag.roles import FileRole, classify_role
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


# Multipliers applied to the fused score. Source is the reference point at 1.0
# and everything else is demoted, never promoted, so this can only reorder
# within a candidate set -- it cannot pull in something retrieval did not find.
#
# The values are round numbers chosen to express an ordering, not fitted
# constants: implementation first, then the configuration that parameterises
# it, then prose about it. Tests are demoted furthest because they are both the
# most numerous competing surface (37% of chunks in this repository from 16% of
# files) and the least likely to be what a question is actually about.
DEFAULT_ROLE_WEIGHTS: Final[dict[FileRole, float]] = {
    FileRole.source: 1.0,
    FileRole.config: 0.8,
    FileRole.docs: 0.7,
    FileRole.test: 0.6,
}


class RoleWeightedReranker:
    """Reorders by fused score scaled by what kind of file a chunk came from.

    Not a relevance model. It encodes one blunt prior -- that a question about
    how something works is usually answered by the implementation rather than
    by a test of it or a document describing it -- and nothing else. It is
    cheap enough to run on every query and explainable enough that a surprising
    ranking can be traced to a single multiplier.

    Unlike the passthrough it *does* set ``rerank_score``, because reranking
    genuinely happened.
    """

    def __init__(self, weights: dict[FileRole, float] | None = None) -> None:
        self._weights = weights or DEFAULT_ROLE_WEIGHTS

    @property
    def name(self) -> str:
        return "role_weighted"

    @property
    def is_passthrough(self) -> bool:
        return False

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        for chunk in candidates:
            role = classify_role(chunk.file_path)
            chunk.rerank_score = chunk.fused_score * self._weights.get(role, 1.0)

        ordered = sorted(
            candidates,
            # Ties broken on chunk id so evaluation runs stay reproducible.
            key=lambda chunk: (-(chunk.rerank_score or 0.0), str(chunk.chunk_id)),
        )
        selected = ordered[:limit]
        for chunk in selected:
            chunk.selected = True
        return selected
