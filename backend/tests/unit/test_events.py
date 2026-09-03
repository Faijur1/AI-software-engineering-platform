"""The indexing event contract and the publisher seam (ADR-004).

The contract is the part worth pinning. A broker can be swapped; a vocabulary
that consumers have already persisted and may replay cannot, so the rules that
protect it are asserted here rather than left to review.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.events import DomainEvent, InProcessEventBus, NullEventPublisher
from app.events import types as events


class Recording:
    """A subscriber that remembers what it was given."""

    def __init__(self, name: str = "recording") -> None:
        self._name = name
        self.seen: list[DomainEvent] = []

    @property
    def name(self) -> str:
        return self._name

    def handle(self, event: DomainEvent) -> None:
        self.seen.append(event)


class Exploding:
    @property
    def name(self) -> str:
        return "exploding"

    def handle(self, event: DomainEvent) -> None:
        raise RuntimeError("this consumer is broken")


def _event(**overrides: Any) -> DomainEvent:
    values: dict[str, Any] = {
        "event_type": events.JOB_STARTED,
        "key": "job-1",
        "sequence": 1,
    }
    values.update(overrides)
    return DomainEvent(**values)


# --- the vocabulary is closed -----------------------------------------------


def test_an_unknown_event_type_is_refused_at_construction() -> None:
    """Caught here rather than downstream.

    An unknown type would be published, persisted and replayed forever before
    anyone noticed consumers quietly skipping it.
    """
    with pytest.raises(ValueError, match="not in the indexing vocabulary"):
        _event(event_type="index.something_invented")


def test_every_declared_type_is_in_the_closed_set() -> None:
    declared = {
        events.JOB_STARTED,
        events.SNAPSHOT_FETCHED,
        events.FILES_DISCOVERED,
        events.CHUNKS_WRITTEN,
        events.EMBEDDINGS_WRITTEN,
        events.JOB_COMPLETED,
        events.JOB_FAILED,
    }
    assert declared == events.INDEXING_EVENT_TYPES


def test_terminal_types_are_a_subset_of_the_vocabulary() -> None:
    assert events.TERMINAL_EVENT_TYPES <= events.INDEXING_EVENT_TYPES
    assert _event(event_type=events.JOB_COMPLETED).is_terminal
    assert _event(event_type=events.JOB_FAILED).is_terminal
    assert not _event(event_type=events.JOB_STARTED).is_terminal


# --- the wire form ----------------------------------------------------------


def test_an_event_survives_a_round_trip_through_its_wire_form() -> None:
    """Replay reads events written by an older producer, so the serialised
    shape has to reconstruct exactly."""
    original = _event(
        event_type=events.CHUNKS_WRITTEN,
        sequence=4,
        payload={"chunks_created": 1008, "languages": {"python": 120}},
        ts=datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        status=None,
        duration_ms=1234,
    )

    restored = DomainEvent.from_dict(original.to_dict())

    assert restored == original


def test_the_partition_key_is_the_job_id() -> None:
    """Ordering is guaranteed within a job because every event for one run
    shares a key and therefore a partition. Nothing compares two runs."""
    assert _event(key="job-42").key == "job-42"


# --- fan-out and isolation --------------------------------------------------


def test_every_subscriber_receives_every_event() -> None:
    bus = InProcessEventBus()
    first, second = Recording("first"), Recording("second")
    bus.subscribe(first)
    bus.subscribe(second)

    bus.publish(_event())

    assert len(first.seen) == 1
    assert len(second.seen) == 1


def test_a_broken_subscriber_does_not_stop_the_others() -> None:
    """The indexing run is the point; the trace is commentary on it.

    A consumer that raises must not take down the producer or its siblings,
    because under Kafka they are separate processes and this bus is standing in
    for that.
    """
    bus = InProcessEventBus()
    bus.subscribe(Exploding())
    survivor = Recording()
    bus.subscribe(survivor)

    bus.publish(_event())

    assert len(survivor.seen) == 1


def test_a_broken_subscriber_does_not_raise_to_the_publisher() -> None:
    bus = InProcessEventBus()
    bus.subscribe(Exploding())

    bus.publish(_event())  # must not raise


def test_the_null_publisher_accepts_and_discards() -> None:
    """It must swallow the event without raising, for paths that should emit
    nothing."""
    NullEventPublisher().publish(_event())
