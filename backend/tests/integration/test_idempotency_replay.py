"""At-least-once delivery, made survivable (ADR-004, milestone 3).

Kafka redelivers. A consumer that crashes between handling an event and
committing its offset sees that event again, and a replay from offset 0 sees
every event again. Neither is an edge case: they are the normal operating
behaviour of a log, and code that assumed exactly-once would corrupt the trace
store on the first rebalance.

The evidence that this was real rather than theoretical is in the migration:
replaying while building milestone 2 left one indexing run holding 78 rows for
a six-event lifecycle -- thirteen copies of each -- and the trace endpoint
served all 78.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select

from app.core.database import session_scope
from app.events import DomainEvent, TraceRecorder
from app.events import types as events
from app.models.agent import Event

pytestmark = pytest.mark.integration


@pytest.fixture
def trace_id() -> Iterator[str]:
    identifier = uuid.uuid4().hex
    yield identifier
    with session_scope() as session:
        session.execute(delete(Event).where(Event.trace_id == identifier))


def _count(trace: str) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count()).select_from(Event).where(Event.trace_id == trace)
            ).scalar_one()
        )


def _event(trace: str, sequence: int = 1, **payload: object) -> DomainEvent:
    return DomainEvent(
        event_type=events.JOB_STARTED,
        key="job-1",
        sequence=sequence,
        trace_id=trace,
        payload=dict(payload),
    )


def test_handling_the_same_event_twice_writes_one_row(trace_id: str) -> None:
    """The core of at-least-once: a redelivery must be absorbed, not doubled."""
    recorder = TraceRecorder()
    event = _event(trace_id)

    recorder.handle(event)
    recorder.handle(event)

    assert _count(trace_id) == 1


def test_a_whole_run_redelivered_stays_a_whole_run(trace_id: str) -> None:
    """What a replay does: every event of a run, again."""
    recorder = TraceRecorder()
    lifecycle = [
        DomainEvent(event_type=t, key="j", sequence=i, trace_id=trace_id)
        for i, t in enumerate(
            [
                events.JOB_STARTED,
                events.SNAPSHOT_FETCHED,
                events.FILES_DISCOVERED,
                events.CHUNKS_WRITTEN,
                events.EMBEDDINGS_WRITTEN,
                events.JOB_COMPLETED,
            ],
            start=1,
        )
    ]

    for _ in range(3):
        for event in lifecycle:
            recorder.handle(event)

    assert _count(trace_id) == 6


def test_a_redelivery_does_not_overwrite_what_was_stored(trace_id: str) -> None:
    """DO NOTHING rather than DO UPDATE, and the difference matters.

    An event is a fact about something that already happened, so a redelivery
    carries nothing newer. Letting it overwrite would allow a corrupted or
    truncated redelivery to replace a good record.
    """
    recorder = TraceRecorder()
    recorder.handle(_event(trace_id, files_seen=226))
    recorder.handle(_event(trace_id, files_seen=0))

    with session_scope() as session:
        stored = session.execute(
            select(Event).where(Event.trace_id == trace_id)
        ).scalar_one()

    assert stored.event_metadata == {"files_seen": 226}


def test_two_runs_may_share_a_sequence_number(trace_id: str) -> None:
    """Uniqueness is per trace, not global.

    Every run numbers its events from one, so a constraint on sequence alone
    would make the second indexing run in the system fail.
    """
    other = uuid.uuid4().hex
    recorder = TraceRecorder()
    try:
        recorder.handle(_event(trace_id, sequence=1))
        recorder.handle(_event(other, sequence=1))

        assert _count(trace_id) == 1
        assert _count(other) == 1
    finally:
        with session_scope() as session:
            session.execute(delete(Event).where(Event.trace_id == other))
