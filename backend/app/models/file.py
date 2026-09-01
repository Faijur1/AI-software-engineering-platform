from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.chunk import CodeChunk
    from app.models.repository import Repository


class File(UUIDPrimaryKey, Timestamps, Base):
    """One indexed source file at a particular commit."""

    __tablename__ = "files"
    # A path appears once per repository. Re-indexing updates the row in place
    # rather than accumulating one row per commit, so the table stays the size
    # of the working tree rather than the size of history.
    __table_args__ = (UniqueConstraint("repository_id", "path", name="uq_file_repo_path"),)

    repository_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    # Repository-relative, forward-slash separated on every platform.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    # None when no tree-sitter grammar covers the file; those are chunked by
    # size instead, and the distinction is what lets evaluation measure the
    # fallback separately (ADR-002).
    language: Mapped[str | None] = mapped_column(String(32))
    # SHA-256 of the file bytes. Drives incremental re-indexing: an unchanged
    # file is not re-parsed.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)

    repository: Mapped[Repository] = relationship()
    chunks: Mapped[list[CodeChunk]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )
