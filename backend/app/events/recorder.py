"""Records indexing events as a trace, independently of the indexing itself.

This is the second consumer ADR-004 makes the condition for adopting Kafka. It
matters that it is genuinely separate rather than logging bolted onto the
indexer: it holds no indexing logic, it can fail without affecting the run, and
in milestone 2 it becomes a consumer group that can be stopped, restarted and
replayed while indexing carries on.

It writes into the existing ``events`` table, so indexing traces are readable
through the same endpoint and the same replay UI the agent already uses. Before
this, that table was agent-only and an indexing run left no trace at all — the
UI showed a progress bar and, once finished, nothing you could inspect.

Each event is written in its own short transaction. That is the same choice the
job's progress updates make, for the same reason: a trace is worth having *while
a run is in flight*, and one that only lands if the run commits is worthless
exactly when it is most needed — after a crash.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.database import session_scope
from app.core.logging import get_logger
from app.events.types import DomainEvent
from app.models.agent import Event

logger = get_logger(__name__)


class TraceRecorder:
    """Writes indexing events into the shared ``events`` table."""

    @property
    def name(self) -> str:
        return "trace-recorder"

    def handle(self, event: DomainEvent) -> None:
        """Persist one event under the trace its producer assigned.

        Stateless on purpose. Holding a trace id would work for the in-process
        bus, which only ever sees one run, and would silently mis-file every
        event once the same code runs as a consumer group across many runs.

        The producer's ``sequence`` is used as-is rather than a counter local to
        this consumer. Under Kafka the consumer may restart mid-run, and a local
        counter would silently renumber the tail of a trace; the producer's
        number is the same on every delivery, which is also what makes
        milestone 3's idempotency check possible.
        """
        with session_scope() as session:
            session.add(
                Event(
                    trace_id=event.trace_id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    component=event.component,
                    ts=event.ts,
                    duration_ms=event.duration_ms,
                    status=event.status,
                    event_metadata=event.payload,
                )
            )


def already_recorded(trace_id: str, sequence: int) -> bool:
    """Whether this trace already holds an event at ``sequence``.

    Not used by the in-process bus, which delivers once. It exists because
    milestone 3 needs it and because writing it here keeps the idempotency
    question next to the code that will have to answer it, rather than
    discovering it later as duplicated rows.
    """
    with session_scope() as session:
        found = session.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.trace_id == trace_id, Event.sequence == sequence)
        ).scalar_one()
    return bool(found)
