"""store encrypted github access token on users

Revision ID: b16807414425
Revises: 5f8ddb1d008d
Create Date: 2026-09-01 18:46:34.071671
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b16807414425'
down_revision: str | None = '5f8ddb1d008d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fernet ciphertext, not the raw token (app/core/security.py). Text rather
    # than a bounded String: the ciphertext length depends on the token length,
    # and GitHub has changed its token format before.
    op.add_column('users', sa.Column('github_token_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'github_token_encrypted')
