"""add files, code_chunks and jobs

Revision ID: 0c12a3ea14d0
Revises: b16807414425
Create Date: 2026-09-01 19:59:35.322923
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0c12a3ea14d0'
down_revision: str | None = 'b16807414425'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The repository -> file -> chunk tree cascades on delete, so disconnecting
    # a repository cannot leave orphaned chunks behind.
    op.create_table('files',
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=32), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('commit_sha', sa.String(length=40), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('repository_id', 'path', name='uq_file_repo_path')
    )
    op.create_index(op.f('ix_files_repository_id'), 'files', ['repository_id'], unique=False)
    op.create_table('jobs',
    sa.Column('type', sa.Enum('index_repository', name='job_type', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='job_status', native_enum=False, length=16), nullable=False),
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_repository_id'), 'jobs', ['repository_id'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_table('code_chunks',
    sa.Column('file_id', sa.UUID(), nullable=False),
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('symbol', sa.String(length=512), nullable=True),
    sa.Column('kind', sa.Enum('function', 'method', 'class_', 'block', 'fragment', 'fallback', name='chunk_kind', native_enum=False, length=16), nullable=False),
    sa.Column('start_line', sa.Integer(), nullable=False),
    sa.Column('end_line', sa.Integer(), nullable=False),
    sa.Column('chunk_hash', sa.String(length=64), nullable=False),
    # Generated in the database, not in Python: it cannot then drift out of
    # sync with content. The GIN index below is what the keyword half of hybrid
    # search will read (milestone 5).
    sa.Column('content_tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', content)", persisted=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chunks_content_tsv', 'code_chunks', ['content_tsv'], unique=False, postgresql_using='gin')
    op.create_index('ix_chunks_repo_hash', 'code_chunks', ['repository_id', 'chunk_hash'], unique=False)
    op.create_index(op.f('ix_code_chunks_file_id'), 'code_chunks', ['file_id'], unique=False)
    op.create_index(op.f('ix_code_chunks_repository_id'), 'code_chunks', ['repository_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_code_chunks_repository_id'), table_name='code_chunks')
    op.drop_index(op.f('ix_code_chunks_file_id'), table_name='code_chunks')
    op.drop_index('ix_chunks_repo_hash', table_name='code_chunks')
    op.drop_index('ix_chunks_content_tsv', table_name='code_chunks', postgresql_using='gin')
    op.drop_table('code_chunks')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_repository_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_files_repository_id'), table_name='files')
    op.drop_table('files')
