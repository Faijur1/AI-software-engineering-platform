"""Vector retrieval: cosine similarity over chunk embeddings.

Strong on conceptual queries ("how is payment retried?"), unreliable on exact
identifiers -- an embedding of ``parse_config`` sits near many similar-looking
names. That weakness is the entire reason the keyword retriever exists beside
it (docs/rag.md).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chunk import CodeChunk
from app.models.file import File
from app.rag.types import Candidate


def search(
    session: Session,
    *,
    repository_id: uuid.UUID,
    query_vector: list[float],
    limit: int,
) -> list[Candidate]:
    """Return the ``limit`` chunks nearest to ``query_vector``.

    Filtered on ``repository_id`` as the first predicate. That is the tenant
    isolation boundary (docs/security.md) and the reason the column is
    denormalised onto chunks -- it must be impossible to forget in a join.

    Chunks with no embedding are excluded rather than treated as distant: a
    partially embedded index should return fewer results, not wrong ones.
    """
    distance = CodeChunk.embedding.cosine_distance(query_vector)

    rows = session.execute(
        select(
            CodeChunk.id,
            File.path,
            CodeChunk.symbol,
            CodeChunk.kind,
            CodeChunk.start_line,
            CodeChunk.end_line,
            CodeChunk.content,
            distance.label("distance"),
        )
        .join(File, File.id == CodeChunk.file_id)
        .where(
            CodeChunk.repository_id == repository_id,
            CodeChunk.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(limit)
    ).all()

    return [
        Candidate(
            chunk_id=row.id,
            file_path=row.path,
            symbol=row.symbol,
            kind=str(row.kind),
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            # Reported as similarity rather than distance: it is what the
            # inspector shows a human, and larger-is-better matches every other
            # score in the system.
            score=1.0 - float(row.distance),
            rank=position,
        )
        for position, row in enumerate(rows, start=1)
    ]
