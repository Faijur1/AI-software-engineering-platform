"""add chunk embeddings and hnsw index

Revision ID: b4bd11b55125
Revises: 0c12a3ea14d0
Create Date: 2026-09-01 21:02:13.751626
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op


revision: str = 'b4bd11b55125'
down_revision: str | None = '0c12a3ea14d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: a chunk exists once parsed and is embedded in a later phase, so
    # NULL means "not embedded yet" and is the work queue the embedding pass
    # reads. The width is fixed here, which is why changing EMBEDDING_DIMENSIONS
    # needs a migration rather than only a restart.
    op.add_column('code_chunks', sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True))
    op.add_column('code_chunks', sa.Column('embedding_model', sa.String(length=64), nullable=True))
    # HNSW rather than IVFFlat: no training step, and it copes with rows being
    # added incrementally, which is exactly how per-repository indexing writes.
    op.create_index('ix_chunks_embedding_hnsw', 'code_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})


def downgrade() -> None:
    op.drop_index('ix_chunks_embedding_hnsw', table_name='code_chunks', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_column('code_chunks', 'embedding_model')
    op.drop_column('code_chunks', 'embedding')
