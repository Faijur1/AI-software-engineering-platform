"""events are unique per trace and sequence

Revision ID: a8863ef27173
Revises: 4981c871f1ad
Create Date: 2026-09-04 21:23:18.217247
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = 'a8863ef27173'
down_revision: str | None = '4981c871f1ad'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing duplicates have to go first, or the constraint cannot be created.
    #
    # They are real, not hypothetical: replaying the Kafka log with a fresh
    # consumer group re-delivers every event, and doing that repeatedly while
    # building milestone 2 left one indexing trace holding 78 rows for a
    # six-event lifecycle -- thirteen copies of each. The trace endpoint served
    # all 78, so the replay UI for that run was unusable.
    #
    # Duplicates are byte-identical by construction: (trace_id, sequence) is
    # assigned by the producer and the payload travels with it, so any copy is
    # as good as any other and ctid picks the physically first.
    op.execute(
        """
        DELETE FROM events a
        USING events b
        WHERE a.ctid > b.ctid
          AND a.trace_id = b.trace_id
          AND a.sequence = b.sequence
        """
    )
    op.create_unique_constraint(
        "uq_events_trace_sequence", "events", ["trace_id", "sequence"]
    )


def downgrade() -> None:
    # The deleted duplicates are not restored, and could not be: they carried no
    # information the surviving row does not.
    op.drop_constraint("uq_events_trace_sequence", "events", type_="unique")
