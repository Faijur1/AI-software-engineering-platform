"""Append-only event trace for an agent run.

Every run emits ordered events under one ``trace_id`` (docs/agents.md). The
trace is a *record* of what happened, never a reconstruction: the Stage 2
replay UI is a view over these rows, so if a step was not recorded as it
occurred, it did not happen as far as the trace is concerned.

Sequence numbers are assigned per trace rather than relying on timestamps.
Two events can land in the same millisecond, and an ordering that sometimes
inverts is worse than no ordering at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.models.agent import Event

# The vocabulary from docs/agents.md. A closed set: an event type that is not
# here will not be rendered by anything downstream, so adding one is a
# deliberate change rather than a passing string.
AGENT_STARTED: Final = "agent.started"
PLAN_CREATED: Final = "agent.plan_created"
ITERATION_STARTED: Final = "agent.iteration_started"
TOOL_STARTED: Final = "tool.started"
TOOL_COMPLETED: Final = "tool.completed"
TOOL_REJECTED: Final = "tool.rejected"
TEST_STARTED: Final = "test.started"
TEST_COMPLETED: Final = "test.completed"
PATCH_CREATED: Final = "patch.created"
AGENT_COMPLETED: Final = "agent.completed"


@dataclass(slots=True)
class Tracer:
    """Records events for one trace, numbering them as they arrive."""

    session: Session
    trace_id: str
    _sequence: int = field(default=0, repr=False)

    def emit(
        self,
        event_type: str,
        *,
        component: str,
        status: str | None = None,
        duration_ms: int | None = None,
        **metadata: Any,
    ) -> Event:
        """Record one event.

        Flushed immediately rather than at the end of the run, so a trace is
        readable *while a run is in flight* and survives a crash mid-run --
        which is when it is most worth having.
        """
        self._sequence += 1
        event = Event(
            trace_id=self.trace_id,
            sequence=self._sequence,
            event_type=event_type,
            component=component,
            ts=datetime.now(UTC),
            duration_ms=duration_ms,
            status=status,
            event_metadata=metadata,
        )
        self.session.add(event)
        self.session.flush()
        return event
