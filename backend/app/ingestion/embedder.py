"""Embed stored chunks and write the vectors back.

A separate phase from parsing, run after chunks are committed. That ordering is
deliberate: embedding is the slow, network-dependent part, and if Ollama is
down the parsed chunks should already be safe in the database rather than
thrown away. Re-running then embeds only what is still missing.

The work queue is a query, not a list held in memory: chunks whose ``embedding``
is NULL, or whose ``embedding_model`` is not the model now configured. That one
predicate covers three cases without any extra bookkeeping -- a first index, a
previous run that failed partway, and a change of embedding model.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.logging import get_logger
from app.llm.base import EmbeddingProvider
from app.models.chunk import CodeChunk

logger = get_logger(__name__)

ProgressCallback = Callable[[int, int], None]


@dataclass(slots=True)
class EmbeddingResult:
    """What an embedding pass did. Counted, never estimated."""

    embedded: int
    skipped_already_current: int
    model: str
    dimensions: int


def count_pending(session: Session, repository_id: uuid.UUID, model: str) -> int:
    """How many chunks still need embedding under ``model``."""
    return int(
        session.execute(
            select(func.count())
            .select_from(CodeChunk)
            .where(CodeChunk.repository_id == repository_id, _pending(model))
        ).scalar_one()
    )


def embed_repository(
    session: Session,
    provider: EmbeddingProvider,
    *,
    repository_id: uuid.UUID,
    batch_size: int = 32,
    on_progress: ProgressCallback | None = None,
) -> EmbeddingResult:
    """Embed every chunk of a repository that is not already current.

    Vectors are written batch by batch and flushed as they go, so a failure
    partway leaves the completed batches valid rather than discarding the whole
    pass. The remaining chunks are still NULL, so a re-run resumes from there.
    """
    report = on_progress or (lambda _done, _total: None)
    model = provider.model_name

    total = count_pending(session, repository_id, model)
    already = int(
        session.execute(
            select(func.count())
            .select_from(CodeChunk)
            .where(CodeChunk.repository_id == repository_id)
        ).scalar_one()
    ) - total

    if total == 0:
        logger.info("embedding_skipped_all_current", repository_id=str(repository_id))
        return EmbeddingResult(0, already, model, provider.dimensions)

    embedded = 0
    report(0, total)

    for batch in _batches(session, repository_id, model, batch_size):
        vectors = provider.embed([chunk.content for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = model
        session.flush()

        embedded += len(batch)
        report(embedded, total)

    logger.info(
        "embedding_complete",
        repository_id=str(repository_id),
        embedded=embedded,
        reused=already,
        model=model,
    )
    return EmbeddingResult(embedded, already, model, provider.dimensions)


def _pending(model: str) -> ColumnElement[bool]:
    """Chunks needing work: never embedded, or embedded by another model."""
    return or_(CodeChunk.embedding.is_(None), CodeChunk.embedding_model != model)


def _batches(
    session: Session, repository_id: uuid.UUID, model: str, size: int
) -> Iterator[list[CodeChunk]]:
    """Yield pending chunks in batches, re-querying as work is completed.

    Re-querying rather than paginating with an offset: each batch stops being
    pending once written, so a fixed offset would skip over chunks. The loop
    terminates because every batch strictly reduces the pending set.
    """
    while True:
        batch = list(
            session.execute(
                select(CodeChunk)
                .where(CodeChunk.repository_id == repository_id, _pending(model))
                .order_by(CodeChunk.created_at, CodeChunk.id)
                .limit(size)
            ).scalars()
        )
        if not batch:
            return
        yield batch
