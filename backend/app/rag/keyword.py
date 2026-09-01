"""Keyword retrieval: PostgreSQL full-text search over chunk content.

Exact where embeddings are vague -- function and class names, error strings,
routes, constants. It reads ``content_tsv``, the generated column, so the
searchable text can never drift out of sync with the content.

Two properties of the ``english`` configuration matter here, and both were
measured against the real index rather than assumed:

``snake_case`` **is** split on underscores. ``github_callback`` indexes as
``'github' 'callback'``, and ``websearch_to_tsquery`` turns the same input into
the phrase ``'github' <-> 'callback'`` -- so an identifier query matches the
definition precisely. This is what recovers the case vector search misses.

``camelCase`` is **not** split. ``OllamaEmbedder`` indexes as one stemmed token,
so a query for ``Ollama`` alone will not match it. That is a real limitation of
this configuration, recorded rather than papered over; measuring whether it
costs anything is milestone 6's job, and a custom tokeniser would be the fix.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, Text, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.chunk import CodeChunk
from app.models.file import File
from app.rag.types import Candidate

logger = get_logger(__name__)

# The text search configuration. Kept as a constant because it must match the
# one the generated column was built with -- a mismatch would silently return
# nothing rather than error.
CONFIGURATION = "english"


def build_query(session: Session, text: str) -> str | None:
    """Turn user text into a tsquery, or None if nothing searchable remains.

    ``websearch_to_tsquery`` is used rather than ``plainto_tsquery`` because it
    accepts what people actually type -- quoted phrases, ``or``, leading
    minus -- without raising on punctuation.

    A query of nothing but stopwords produces an empty tsquery, which matches
    every row with rank zero. Returning None instead lets the caller record
    that the keyword half contributed nothing, rather than quietly polluting
    the merge with noise.
    """
    rendered = session.execute(
        select(func.websearch_to_tsquery(CONFIGURATION, text).cast(Text))
    ).scalar_one()

    cleaned = (rendered or "").strip()
    return cleaned or None


def relax(rendered: str) -> str:
    """Turn an all-terms tsquery into an any-terms one.

    ``websearch_to_tsquery`` joins terms with AND, so a natural-language
    question only matches a chunk containing *every* word. For prose questions
    over code that is far too strict -- "where is the oauth callback handled"
    requires a chunk containing all of oauth, callback and handled.

    Swapping the AND operators for ORs keeps the phrase groupings (``<->``)
    intact, so quoted phrases and split identifiers still match as units.
    """
    return rendered.replace(" & ", " | ")


def search(
    session: Session,
    *,
    repository_id: uuid.UUID,
    query_text: str,
    limit: int,
) -> list[Candidate]:
    """Return the ``limit`` best full-text matches for ``query_text``.

    Tries the strict all-terms query first and falls back to any-terms only if
    it matched nothing: precision when it is available, recall when it is not.
    A two-stage fallback rather than always ORing, because when every term is
    present that really is the better match, and ORing would bury it under
    chunks that share one common word.
    """
    rendered = build_query(session, query_text)
    if rendered is None:
        return []

    rows = _run(session, repository_id, func.to_tsquery(CONFIGURATION, rendered), limit)
    if not rows:
        relaxed = relax(rendered)
        if relaxed != rendered:
            rows = _run(session, repository_id, func.to_tsquery(CONFIGURATION, relaxed), limit)

    return [
        Candidate(
            chunk_id=row.id,
            file_path=row.path,
            symbol=row.symbol,
            kind=str(row.kind),
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            score=float(row.rank),
            rank=position,
        )
        for position, row in enumerate(rows, start=1)
    ]


def _run(session: Session, repository_id: uuid.UUID, tsquery: object, limit: int) -> list[Row[Any]]:
    rank = func.ts_rank(CodeChunk.content_tsv, tsquery)
    return list(
        session.execute(
            select(
                CodeChunk.id,
                File.path,
                CodeChunk.symbol,
                CodeChunk.kind,
                CodeChunk.start_line,
                CodeChunk.end_line,
                CodeChunk.content,
                rank.label("rank"),
            )
            .join(File, File.id == CodeChunk.file_id)
            .where(
                # Repository isolation first, as everywhere else.
                CodeChunk.repository_id == repository_id,
                CodeChunk.content_tsv.op("@@")(tsquery),
            )
            .order_by(rank.desc())
            .limit(limit)
        ).all()
    )
