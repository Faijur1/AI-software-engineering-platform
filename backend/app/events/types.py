"""The indexing event vocabulary, and the shape an event travels in.

This is the contract ADR-004 anticipated. It exists as its own module, ahead of
any Kafka, because the hard part of an event log is not the broker — it is
agreeing what an event *is* and refusing to change it casually. Getting that
wrong is expensive later, when a replay has to interpret events written by an
older producer.

Three rules the vocabulary is built on:

**A closed set.** An event type not named here will not be rendered by anything
downstream, so adding one is a deliberate change rather than a passing string.
The agent trace vocabulary in ``app/agent/tracing.py`` follows the same rule and
this mirrors it on purpose.

**Events are facts, not instructions.** ``index.files_discovered`` says what was
found; it does not tell a consumer to do anything. A consumer decides for
itself. That is what makes a second consumer possible without touching the
first, which is the whole reason for the exercise.

**Payloads carry counts, never content.** An event may say that 182 files were
indexed; it may not carry the files. Repository content is the user's code, and
an event log is durable and replayable — content in it would outlive every
control that governs where that code is allowed to go (docs/security.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

# --- the closed set ---------------------------------------------------------

JOB_STARTED: Final = "index.job_started"
SNAPSHOT_FETCHED: Final = "index.snapshot_fetched"
FILES_DISCOVERED: Final = "index.files_discovered"
CHUNKS_WRITTEN: Final = "index.chunks_written"
EMBEDDINGS_WRITTEN: Final = "index.embeddings_written"
JOB_COMPLETED: Final = "index.job_completed"
JOB_FAILED: Final = "index.job_failed"

INDEXING_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        JOB_STARTED,
        SNAPSHOT_FETCHED,
        FILES_DISCOVERED,
        CHUNKS_WRITTEN,
        EMBEDDINGS_WRITTEN,
        JOB_COMPLETED,
        JOB_FAILED,
    }
)

# Terminal types. A consumer that tracks progress uses these to know a job will
# produce nothing further, rather than inferring it from a timeout.
TERMINAL_EVENT_TYPES: Final[frozenset[str]] = frozenset({JOB_COMPLETED, JOB_FAILED})

# The Kafka topic these will be published to in milestone 2. Named here so the
# contract and its destination stay in one place.
INDEXING_TOPIC: Final = "aisep.indexing.v1"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """One fact about an indexing run.

    ``key`` is the partition key. It is the **job id**, so every event for a
    single run lands on one partition and is therefore delivered in order.
    Ordering across different jobs is not guaranteed and is not needed: nothing
    downstream compares two runs.

    ``sequence`` is assigned by the producer rather than derived from ``ts``.
    Two events can share a millisecond, and an ordering that sometimes inverts
    is worse than no ordering at all.
    """

    event_type: str
    key: str
    sequence: int
    # The run this event belongs to. Carried on the event rather than held by
    # the consumer: the in-process bus sees one run and could hold it, but a
    # consumer group sees every run and cannot. Putting it here is what makes
    # both backends record a run under the same trace id, which is the whole
    # point of the two being interchangeable.
    trace_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    component: str = "ingestion"
    status: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if self.event_type not in INDEXING_EVENT_TYPES:
            # Raised rather than logged. An unknown type would be published,
            # persisted and replayed forever before anyone noticed a consumer
            # silently skipping it.
            raise ValueError(
                f"{self.event_type!r} is not in the indexing vocabulary. "
                "Add it to types.py deliberately, or fix the caller."
            )

    @property
    def is_terminal(self) -> bool:
        return self.event_type in TERMINAL_EVENT_TYPES

    def to_dict(self) -> dict[str, Any]:
        """The wire form. Kept explicit so the serialised shape is reviewable."""
        return {
            "event_type": self.event_type,
            "key": self.key,
            "sequence": self.sequence,
            "trace_id": self.trace_id,
            "ts": self.ts.isoformat(),
            "component": self.component,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DomainEvent:
        """Rebuild from the wire form, for a consumer or a replay."""
        return cls(
            event_type=str(raw["event_type"]),
            key=str(raw["key"]),
            sequence=int(raw["sequence"]),
            trace_id=str(raw.get("trace_id") or ""),
            payload=dict(raw.get("payload") or {}),
            ts=datetime.fromisoformat(str(raw["ts"])),
            component=str(raw.get("component") or "ingestion"),
            status=raw.get("status"),
            duration_ms=raw.get("duration_ms"),
        )
