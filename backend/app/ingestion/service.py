"""Orchestrates a full indexing run: fetch, discover, filter, parse, chunk, store.

Structured so each phase is independently testable and the whole is driven by a
callback for progress, rather than reaching for the job row itself. That keeps
this module free of any knowledge of how progress is reported.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingestion.chunker import chunk_source
from app.ingestion.discovery import DiscoveredFile, discover
from app.ingestion.fetcher import fetch_snapshot
from app.models.chunk import CodeChunk
from app.models.file import File

logger = get_logger(__name__)

ProgressCallback = Callable[[int, str], None]

# Progress is reported against these coarse stage boundaries. Deliberately not
# a fine-grained estimate: an inaccurate percentage is worse than a rough one.
_PROGRESS_FETCHED: Final = 15
_PROGRESS_DISCOVERED: Final = 30
_PROGRESS_PARSED: Final = 90


@dataclass(slots=True)
class IndexResult:
    """What an indexing run actually did. Every number is counted, not estimated."""

    commit_sha: str
    files_seen: int
    files_indexed: int
    files_skipped: int
    files_unchanged: int
    chunks_created: int
    chunks_deleted: int
    languages: dict[str, int]
    fallback_files: int


def index_repository(
    session: Session,
    *,
    repository_id: uuid.UUID,
    owner: str,
    name: str,
    ref: str,
    token: str,
    on_progress: ProgressCallback | None = None,
) -> IndexResult:
    """Index one repository at ``ref``, replacing its previous index."""
    report_progress = on_progress or (lambda _pct, _stage: None)

    report_progress(0, "fetching repository")
    with fetch_snapshot(token, owner, name, ref) as snapshot:
        report_progress(_PROGRESS_FETCHED, "discovering files")
        found = discover(
            snapshot.root, extra_excluded_dirs=get_settings().extra_excluded_directories
        )

        report_progress(_PROGRESS_DISCOVERED, "parsing and chunking")
        result = _persist(
            session,
            repository_id=repository_id,
            commit_sha=snapshot.commit_sha,
            files=found.included,
            report_progress=report_progress,
        )
        result.files_seen = found.examined
        result.files_skipped = sum(found.skipped.values())

    report_progress(100, "complete")
    logger.info(
        "index_complete",
        repository=f"{owner}/{name}",
        commit=result.commit_sha[:12],
        files_indexed=result.files_indexed,
        files_unchanged=result.files_unchanged,
        chunks=result.chunks_created,
    )
    return result


def _persist(
    session: Session,
    *,
    repository_id: uuid.UUID,
    commit_sha: str,
    files: list[DiscoveredFile],
    report_progress: ProgressCallback,
) -> IndexResult:
    """Write files and chunks, reusing rows for files that have not changed."""
    existing = {
        row.path: row
        for row in session.execute(
            select(File).where(File.repository_id == repository_id)
        ).scalars()
    }

    result = IndexResult(
        commit_sha=commit_sha,
        files_seen=0,
        files_indexed=0,
        files_skipped=0,
        files_unchanged=0,
        chunks_created=0,
        chunks_deleted=0,
        languages={},
        fallback_files=0,
    )

    seen_paths: set[str] = set()
    span = _PROGRESS_PARSED - _PROGRESS_DISCOVERED

    for position, found in enumerate(files):
        seen_paths.add(found.path)
        content_hash = hashlib.sha256(found.content.encode("utf-8")).hexdigest()
        row = existing.get(found.path)

        if row is not None and row.content_hash == content_hash:
            # Unchanged since the last run: leave the row and its chunks alone.
            # This is the whole point of storing the hash -- from milestone 4 it
            # is also what avoids paying to re-embed identical content.
            row.commit_sha = commit_sha
            result.files_unchanged += 1
            continue

        if row is None:
            row = File(
                repository_id=repository_id,
                path=found.path,
                language=found.language,
                content_hash=content_hash,
                commit_sha=commit_sha,
                size_bytes=found.size_bytes,
            )
            session.add(row)
            session.flush()
        else:
            # Changed: drop the old chunks before writing the new ones, so a
            # deleted function cannot linger in the index and be cited.
            # Counting before deleting: a CursorResult rowcount is not part
            # of the typed Result API, and the count is only ever reported.
            result.chunks_deleted += (
                session.execute(
                    select(func.count())
                    .select_from(CodeChunk)
                    .where(CodeChunk.file_id == row.id)
                ).scalar_one()
            )
            session.execute(delete(CodeChunk).where(CodeChunk.file_id == row.id))
            row.language = found.language
            row.content_hash = content_hash
            row.commit_sha = commit_sha
            row.size_bytes = found.size_bytes

        chunks = chunk_source(found.content, found.language)
        for chunk in chunks:
            session.add(
                CodeChunk(
                    file_id=row.id,
                    repository_id=repository_id,
                    content=chunk.content,
                    symbol=chunk.symbol,
                    kind=chunk.kind,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    chunk_hash=chunk.chunk_hash,
                )
            )

        result.files_indexed += 1
        result.chunks_created += len(chunks)
        key = found.language or "(no grammar)"
        result.languages[key] = result.languages.get(key, 0) + 1
        if found.language is None:
            result.fallback_files += 1

        if files and position % 25 == 0:
            report_progress(
                _PROGRESS_DISCOVERED + int(span * position / len(files)),
                "parsing and chunking",
            )

    # Files present in the previous index but gone from this commit. Removing
    # them is what stops a deleted file being retrieved and cited as if it
    # still existed.
    removed = [row for path, row in existing.items() if path not in seen_paths]
    for row in removed:
        session.delete(row)
    if removed:
        logger.info("index_removed_files", count=len(removed))

    session.flush()
    return result
