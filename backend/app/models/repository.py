from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.user import User


class IndexStatus(StrEnum):
    not_indexed = "not_indexed"
    queued = "queued"
    indexing = "indexing"
    indexed = "indexed"
    failed = "failed"


class Repository(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "repositories"
    # A user may connect a given GitHub repository once. Two different users
    # may each connect the same public repository independently.
    __table_args__ = (UniqueConstraint("user_id", "github_id", name="uq_repo_user_github"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    is_private: Mapped[bool] = mapped_column(default=False, nullable=False)

    # SHA of the commit the current index was built from. Drives incremental
    # re-indexing: unchanged files are not re-parsed or re-embedded.
    current_commit: Mapped[str | None] = mapped_column(String(40))
    index_status: Mapped[IndexStatus] = mapped_column(
        Enum(IndexStatus, name="index_status", native_enum=False, length=32),
        default=IndexStatus.not_indexed,
        nullable=False,
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="repositories")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"
