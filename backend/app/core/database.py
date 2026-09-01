"""Database engine, session factory, and the declarative base.

Sessions are synchronous by deliberate choice (see docs/adr/ADR-008). The API
and the RQ workers therefore share one session pattern and one set of models.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


_settings = get_settings()

engine = create_engine(
    str(_settings.database_url),
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_pool_max_overflow,
    # Recycle connections that a proxy or the server may have dropped, rather
    # than surfacing a stale-connection error to the caller.
    pool_pre_ping=True,
    future=True,
)

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope for a unit of work.

    Commits on success, rolls back on any exception, and always closes. The
    exception is re-raised — failures are never swallowed here.
    """
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as session:
        yield session
