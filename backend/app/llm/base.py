"""Provider-agnostic interface for embeddings.

A Protocol rather than a base class: ingestion depends on the shape, so a test
double needs no inheritance and a future provider need not import from here.

The interface carries ``model_name`` and ``dimensions`` deliberately. Both are
recorded alongside every stored vector, because an embedding is only comparable
to others produced by the same model at the same width -- mixing them silently
degrades retrieval in a way that is very hard to diagnose later.
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Turns text into vectors."""

    @property
    def model_name(self) -> str:
        """Identifier stored with every vector this provider produces."""
        ...

    @property
    def dimensions(self) -> int:
        """Vector width. Must match the database column."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, returning one vector per input in the same order.

        Implementations must either return exactly ``len(texts)`` vectors of
        exactly ``dimensions`` width, or raise. Returning a short or ragged
        result would silently misalign vectors with their chunks.
        """
        ...
