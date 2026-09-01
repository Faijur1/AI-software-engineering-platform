from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.repository import Repository


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    # GitHub IDs are not guaranteed to fit in a 32-bit integer.
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024))

    # The GitHub OAuth access token, Fernet-encrypted (app.core.security). Never
    # stored in plaintext, never logged, and never returned by any endpoint.
    # Nullable so a user row survives a sign-out that revokes the token.
    github_token_encrypted: Mapped[str | None] = mapped_column(Text)

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
