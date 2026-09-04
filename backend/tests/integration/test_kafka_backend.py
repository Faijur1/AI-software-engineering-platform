"""Kafka as the indexing event log (ADR-004), against a real broker.

The test that matters is the contract one: the *same* run published through
either backend has to produce the same trace. If it does not, the seam is
decorative and the two backends are merely similar.

Marked ``kafka`` so it is skipped unless a broker is running. Kafka is
profile-gated in docker-compose precisely because it does not run all day on a
7.7 GB machine, and a test suite that fails when it is absent would make the
default checkout look broken.

    docker compose --profile kafka up -d
    pytest -m kafka
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, select

from app.core.config import Settings, get_settings
from app.core.database import session_scope
from app.events import DomainEvent, InProcessEventBus, TraceRecorder
from app.events import types as events
from app.events.kafka import KafkaEventPublisher, consume
from app.events.topics import describe, ensure_topics
from app.models.agent import Event

pytestmark = pytest.mark.kafka


@pytest.fixture(scope="module", autouse=True)
def _topic() -> None:
    """Create the topic explicitly, as the worker does at start-up."""
    ensure_topics()


@pytest.fixture
def trace_id() -> Iterator[str]:
    identifier = uuid.uuid4().hex
    yield identifier
    with session_scope() as session:
        session.execute(delete(Event).where(Event.trace_id == identifier))


def _lifecycle(trace: str, job_id: str) -> list[DomainEvent]:
    """One complete indexing run, the six facts a real run emits."""
    stages: list[tuple[str, dict[str, object]]] = [
        (events.JOB_STARTED, {"repository": "alice/sample", "ref": "main"}),
        (events.SNAPSHOT_FETCHED, {"commit": "b" * 40}),
        (events.FILES_DISCOVERED, {"files_seen": 226, "files_indexed": 22}),
        (events.CHUNKS_WRITTEN, {"chunks_created": 303, "chunks_deleted": 83}),
        (events.EMBEDDINGS_WRITTEN, {}),
        (events.JOB_COMPLETED, {"commit": "b" * 40}),
    ]
    return [
        DomainEvent(
            event_type=event_type,
            key=job_id,
            sequence=index,
            trace_id=trace,
            payload=payload,
        )
        for index, (event_type, payload) in enumerate(stages, start=1)
    ]


def _recorded(trace: str) -> list[Event]:
    with session_scope() as session:
        return list(
            session.execute(
                select(Event).where(Event.trace_id == trace).order_by(Event.sequence)
            ).scalars()
        )


def _kafka_settings() -> Settings:
    return get_settings()


# --- the broker is really there ---------------------------------------------


def test_the_topic_exists_with_the_configured_shape() -> None:
    """Created explicitly rather than auto-created, so its shape is a decision.

    Partition count cannot be reduced later, which is why it is not left to a
    broker default nobody chose.
    """
    described = describe()

    assert described["exists"] is True
    assert described["partitions"] == 1
    assert described["brokers"] >= 1


def test_creating_the_topic_twice_is_harmless() -> None:
    """Every worker calls this at start-up, so it has to be idempotent."""
    first = ensure_topics()
    second = ensure_topics()

    assert events.INDEXING_TOPIC in first
    assert events.INDEXING_TOPIC in second


# --- the contract: both backends, one result --------------------------------


def test_a_run_published_through_kafka_is_recorded_identically(trace_id: str) -> None:
    """The property the seam exists for.

    Publish a run to the broker, consume it with the same recorder the
    in-process bus uses, and the rows must be indistinguishable from the
    in-process path -- same trace, same order, same sequence numbers.
    """
    job_id = str(uuid.uuid4())
    publisher = KafkaEventPublisher(_kafka_settings())
    for event in _lifecycle(trace_id, job_id):
        publisher.publish(event)

    # A group of its own, reading the log from the beginning. Sharing the
    # worker's group would mean consuming whatever earlier runs left unread and
    # asserting on someone else's backlog.
    handled = consume(
        TraceRecorder(), group_id=f"test-{uuid.uuid4().hex}", from_start=True, until_drained=True
    )

    assert handled >= 6
    recorded = _recorded(trace_id)
    assert [e.event_type for e in recorded] == [
        events.JOB_STARTED,
        events.SNAPSHOT_FETCHED,
        events.FILES_DISCOVERED,
        events.CHUNKS_WRITTEN,
        events.EMBEDDINGS_WRITTEN,
        events.JOB_COMPLETED,
    ]
    assert [e.sequence for e in recorded] == [1, 2, 3, 4, 5, 6]


def test_both_backends_produce_the_same_trace(trace_id: str) -> None:
    """Run the identical lifecycle through each backend and compare.

    This is the assertion that would fail if, say, the trace id were held by
    the consumer instead of carried on the event: the in-process path would
    still look right and the Kafka path would file every event under the wrong
    trace.
    """
    job_id = str(uuid.uuid4())
    in_process_trace = f"{trace_id}a"[:32]
    kafka_trace = f"{trace_id}b"[:32]

    bus = InProcessEventBus()
    bus.subscribe(TraceRecorder())
    for event in _lifecycle(in_process_trace, job_id):
        bus.publish(event)

    publisher = KafkaEventPublisher(_kafka_settings())
    for event in _lifecycle(kafka_trace, job_id):
        publisher.publish(event)
    consume(
        TraceRecorder(),
        group_id=f"test-{uuid.uuid4().hex}",
        from_start=True,
        until_drained=True,
    )

    def shape(rows: list[Event]) -> list[tuple[int, str, str | None]]:
        return [(r.sequence, r.event_type, r.status) for r in rows]

    try:
        assert shape(_recorded(in_process_trace)) == shape(_recorded(kafka_trace))
    finally:
        with session_scope() as session:
            session.execute(
                delete(Event).where(Event.trace_id.in_([in_process_trace, kafka_trace]))
            )


def test_the_payload_survives_the_wire(trace_id: str) -> None:
    """Counts have to arrive intact; a trace of empty payloads is no trace."""
    job_id = str(uuid.uuid4())
    publisher = KafkaEventPublisher(_kafka_settings())
    publisher.publish(
        DomainEvent(
            event_type=events.FILES_DISCOVERED,
            key=job_id,
            sequence=1,
            trace_id=trace_id,
            payload={"files_seen": 226, "files_indexed": 22, "files_unchanged": 190},
        )
    )

    consume(
        TraceRecorder(),
        group_id=f"test-{uuid.uuid4().hex}",
        from_start=True,
        until_drained=True,
    )

    recorded = _recorded(trace_id)
    assert len(recorded) == 1
    assert recorded[0].event_metadata == {
        "files_seen": 226,
        "files_indexed": 22,
        "files_unchanged": 190,
    }


# --- the job queue seam (ADR-003) -------------------------------------------


def test_a_job_dispatched_through_kafka_reaches_a_worker() -> None:
    """The ADR-003 interface was claimed to be a real seam. This is the check.

    The job id is published and consumed; the consumer calls the same
    entrypoint RQ would have called. What is asserted is the round trip, not
    the indexing itself -- a job id that no longer exists fails inside the
    worker and is committed past, which is the behaviour that stops one bad
    job blocking the partition.
    """
    from app.queue.kafka_backend import KafkaJobQueue, consume_jobs

    queue = KafkaJobQueue(_kafka_settings())
    job_id = uuid.uuid4()
    queue.enqueue_index_repository(job_id)

    handled = consume_jobs(max_jobs=1, until_drained=True)

    assert handled >= 1


def test_agent_runs_are_not_carried_on_kafka() -> None:
    """ADR-004 excludes agent reasoning: it is a state machine, not a stream.

    A backend that quietly moved an excluded workload onto a different
    transport would be doing the opposite of what the interface is for, so the
    delegation to RQ is pinned rather than assumed.
    """
    import inspect

    from app.queue.kafka_backend import KafkaJobQueue

    source = inspect.getsource(KafkaJobQueue.enqueue_agent_run)

    assert "get_rq_queue" in source


# --- replay and redelivery, through the broker ------------------------------


def test_consuming_the_same_log_twice_writes_the_rows_once(trace_id: str) -> None:
    """Replay rebuilds the trace store rather than multiplying it.

    Two independent consumer groups read the same log from offset 0, so every
    event is delivered twice in total. Before the uniqueness constraint this
    doubled the rows -- the migration that added it had to delete 917 of them.
    """
    job_id = str(uuid.uuid4())
    publisher = KafkaEventPublisher(_kafka_settings())
    for event in _lifecycle(trace_id, job_id):
        publisher.publish(event)

    first = consume(
        TraceRecorder(),
        group_id=f"replay-a-{uuid.uuid4().hex}",
        from_start=True,
        until_drained=True,
    )
    after_first = len(_recorded(trace_id))

    second = consume(
        TraceRecorder(),
        group_id=f"replay-b-{uuid.uuid4().hex}",
        from_start=True,
        until_drained=True,
    )
    after_second = len(_recorded(trace_id))

    assert first >= 6 and second >= 6, "both groups must have seen the events"
    assert after_first == 6
    assert after_second == 6, "the second pass must write nothing new"


def test_replay_reports_delivered_and_written_separately(trace_id: str) -> None:
    """The two numbers differing is the demonstration.

    A healthy replay delivers everything and writes nothing, and a caller can
    only see that if the counts are reported apart rather than collapsed into
    one "processed" figure.
    """
    from app.events.replay import replay

    job_id = str(uuid.uuid4())
    publisher = KafkaEventPublisher(_kafka_settings())
    for event in _lifecycle(trace_id, job_id):
        publisher.publish(event)

    first = replay(_kafka_settings())
    second = replay(_kafka_settings())

    assert first.events_delivered >= 6
    assert second.events_delivered == first.events_delivered, "same log, same delivery"
    assert second.rows_written == 0, "nothing new on a second pass"


def test_a_replay_leaves_the_workers_offsets_alone(trace_id: str) -> None:
    """Replay uses a fresh group so the running worker keeps its position.

    Resetting the worker's offsets instead would make a replay and a restart
    indistinguishable, and would reprocess the backlog on the worker's own
    consumer while it was trying to keep up with live events.
    """
    import inspect

    from app.events import replay as replay_module

    source = inspect.getsource(replay_module.replay)

    assert "group_id or f\"replay-" in source
