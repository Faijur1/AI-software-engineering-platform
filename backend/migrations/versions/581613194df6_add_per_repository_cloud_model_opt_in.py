"""add per-repository cloud model opt-in

Revision ID: 581613194df6
Revises: f64bc84dfbd5
Create Date: 2026-09-03 02:26:20.495605
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '581613194df6'
down_revision: str | None = 'f64bc84dfbd5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default=false is required, not cosmetic: autogenerate produced a
    # NOT NULL column with no default, which fails outright on a table that
    # already has rows. It also fixes the value every existing repository gets,
    # and that value must be deny -- a repository indexed before any cloud
    # provider existed was never offered the choice, so it cannot be treated as
    # having made one.
    #
    # The default is left in place afterwards rather than dropped, so a row
    # inserted by anything that does not know about this column is also denied.
    op.add_column(
        'repositories',
        sa.Column(
            'allow_cloud_llm',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'repositories',
        sa.Column('cloud_llm_allowed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Downgrading discards recorded consent. That is the correct direction to
    # fail in: the columns going away means no repository is opted in, and the
    # code at that revision cannot send content to a cloud provider anyway.
    op.drop_column('repositories', 'cloud_llm_allowed_at')
    op.drop_column('repositories', 'allow_cloud_llm')
