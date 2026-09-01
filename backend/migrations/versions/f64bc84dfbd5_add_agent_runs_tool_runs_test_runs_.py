"""add agent runs, tool runs, test runs, patches and events

Revision ID: f64bc84dfbd5
Revises: b4bd11b55125
Create Date: 2026-09-02 01:32:46.416605
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f64bc84dfbd5'
down_revision: str | None = 'b4bd11b55125'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The agent tree: a run owns its tool calls, test runs and patches, all
    # cascading on delete. Events are keyed by trace_id rather than by a
    # foreign key, because a trace spans components and is append-only.
    op.create_table('events',
    sa.Column('trace_id', sa.String(length=32), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('component', sa.String(length=64), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_events_trace_id'), 'events', ['trace_id'], unique=False)
    op.create_table('agent_runs',
    sa.Column('trace_id', sa.String(length=32), nullable=False),
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('task', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', 'max_iterations_exceeded', name='agent_status', native_enum=False, length=32), nullable=False),
    sa.Column('plan', sa.Text(), nullable=True),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('iterations', sa.Integer(), nullable=False),
    sa.Column('max_iterations', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_runs_repository_id'), 'agent_runs', ['repository_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_status'), 'agent_runs', ['status'], unique=False)
    op.create_index(op.f('ix_agent_runs_trace_id'), 'agent_runs', ['trace_id'], unique=False)
    op.create_table('patches',
    sa.Column('agent_run_id', sa.UUID(), nullable=False),
    sa.Column('diff', sa.Text(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('proposed', 'approved', 'rejected', name='patch_status', native_enum=False, length=16), nullable=False),
    sa.Column('validated', sa.Boolean(), nullable=True),
    sa.Column('validation_output', sa.Text(), nullable=True),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_patches_agent_run_id'), 'patches', ['agent_run_id'], unique=False)
    op.create_index(op.f('ix_patches_status'), 'patches', ['status'], unique=False)
    op.create_table('test_runs',
    sa.Column('agent_run_id', sa.UUID(), nullable=False),
    sa.Column('command', sa.Text(), nullable=False),
    sa.Column('exit_code', sa.Integer(), nullable=False),
    sa.Column('timed_out', sa.Boolean(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('stdout', sa.Text(), nullable=False),
    sa.Column('stderr', sa.Text(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_test_runs_agent_run_id'), 'test_runs', ['agent_run_id'], unique=False)
    op.create_table('tool_runs',
    sa.Column('agent_run_id', sa.UUID(), nullable=False),
    sa.Column('iteration', sa.Integer(), nullable=False),
    sa.Column('tool_name', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('succeeded', 'failed', 'rejected', name='tool_status', native_enum=False, length=16), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('input', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_runs_agent_run_id'), 'tool_runs', ['agent_run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tool_runs_agent_run_id'), table_name='tool_runs')
    op.drop_table('tool_runs')
    op.drop_index(op.f('ix_test_runs_agent_run_id'), table_name='test_runs')
    op.drop_table('test_runs')
    op.drop_index(op.f('ix_patches_status'), table_name='patches')
    op.drop_index(op.f('ix_patches_agent_run_id'), table_name='patches')
    op.drop_table('patches')
    op.drop_index(op.f('ix_agent_runs_trace_id'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_status'), table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_repository_id'), table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_index(op.f('ix_events_trace_id'), table_name='events')
    op.drop_table('events')
