"""Rebuild the trace store from the event log (ADR-004).

Replay is the property that distinguishes a log from a queue, and the reason
ADR-004 chose Kafka over SNS/SQS. A queue forgets a message once it is consumed;
a log keeps it, so the answer to "the consumer had a bug and wrote the wrong
rows for a week" is to fix the consumer and read the week again.

Replay is only safe because the recorder is idempotent. Reading the log from
offset 0 re-delivers every event that has ever been published, so without the
uniqueness constraint on ``(trace_id, sequence)`` a replay would multiply the
trace store rather than rebuild it. That is not a hypothetical: doing exactly
this while building milestone 2 left one indexing run holding 78 rows for a
six-event lifecycle.

A replay uses a **fresh consumer group** rather than resetting the worker's
offsets. The worker keeps running and keeps its own position; the replay reads
the same log independently and disappears when it finishes. Two consumers of one
log, at their own pace, is the thing Kafka is for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.events.kafka import consume
from app.events.recorder import TraceRecorder

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """What a replay did, in terms a person can check."""

    events_delivered: int
    rows_before: int
    rows_after: int

    @property
    def rows_written(self) -> int:
        """New rows. Zero on a replay of an already-complete store, which is
        the outcome that proves idempotency rather than merely claiming it."""
        return self.rows_after - self.rows_before


def replay(settings: Settings | None = None, *, group_id: str | None = None) -> ReplayResult:
    """Read the whole log and record everything in it.

    Returns counts rather than logging them, so a caller -- a test, or the CLI
    -- can assert on the difference between events delivered and rows written.
    Those two numbers being different is the entire point: a healthy replay
    delivers hundreds and writes none.
    """
    from sqlalchemy import func, select

    from app.core.database import session_scope
    from app.models.agent import Event

    resolved = settings or get_settings()

    def count_rows() -> int:
        with session_scope() as session:
            return int(session.execute(select(func.count()).select_from(Event)).scalar_one())

    before = count_rows()
    delivered = consume(
        TraceRecorder(),
        settings=resolved,
        # A group that has never existed, so it starts at the beginning and
        # leaves the worker's offsets untouched.
        group_id=group_id or f"replay-{uuid.uuid4().hex[:12]}",
        from_start=True,
        until_drained=True,
    )
    after = count_rows()

    logger.info(
        "event_replay_complete",
        delivered=delivered,
        rows_written=after - before,
    )
    return ReplayResult(events_delivered=delivered, rows_before=before, rows_after=after)
