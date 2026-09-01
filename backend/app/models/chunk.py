from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Computed, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.file import File


class ChunkKind(StrEnum):
    """What the chunk corresponds to in the source.

    ``block`` is a run of module-level statements (imports, constants).
    ``fragment`` is part of an oversized unit that had to be split, and
    ``fallback`` is size-based chunking of a file with no grammar -- both are
    named so retrieval quality can be measured separately for them (ADR-002).
    """

    function = "function"
    method = "method"
    class_ = "class"
    block = "block"
    fragment = "fragment"
    fallback = "fallback"


class CodeChunk(UUIDPrimaryKey, Timestamps, Base):
    """A logical unit of code: the unit of retrieval and of citation."""

    __tablename__ = "code_chunks"
    __table_args__ = (
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        Index("ix_chunks_repo_hash", "repository_id", "chunk_hash"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    # Denormalised from files deliberately: every retrieval query filters on it,
    # and as a column that check is one indexed predicate rather than a join
    # somebody can forget to write (docs/database.md).
    repository_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Qualified where it helps retrieval, e.g. "Thing.method". None for blocks.
    symbol: Mapped[str | None] = mapped_column(String(512))
    kind: Mapped[ChunkKind] = mapped_column(
        Enum(ChunkKind, name="chunk_kind", native_enum=False, length=16), nullable=False
    )
    # 1-based and inclusive, matching how editors and GitHub number lines. These
    # are what make a citation point at real code.
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    # SHA-256 of the content. An unchanged hash means the chunk need not be
    # re-embedded when the repository is re-indexed. Indexed only through the
    # composite below: every lookup is already scoped to a repository, so a
    # second standalone index on this column would never be the one chosen.
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Generated in the database so it cannot drift out of sync with content.
    # The keyword half of hybrid search reads this directly (milestone 5).
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )

    file: Mapped[File] = relationship(back_populates="chunks")
