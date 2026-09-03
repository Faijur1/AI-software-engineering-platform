"""Where indexing events go, and who gets them.

The seam ADR-004 named. `EventPublisher` is a Protocol for the same reason
`JobQueue` is: the caller depends on the shape, so the Kafka implementation in
milestone 2 need not import anything from here, and a test double needs no
inheritance.

`InProcessEventBus` is the milestone 1 implementation and is deliberately
honest about what it is *not*. It fans out synchronously, in the producer's own
process, with no durability and no replay. That is enough to prove the contract
and to let a second consumer exist, and it is exactly what Kafka replaces:

| | in process | Kafka (milestone 2) |
| --- | --- | --- |
| durability | none, lost on crash | on disk, retained |
| replay | impossible | from any offset |
| a slow consumer | blocks the producer | falls behind alone |
| delivery | exactly once, trivially | at least once, needs idempotency |

The last row is the one that will cost work later, and it is worth stating now:
code written against this bus can assume it sees every event once, and that
assumption breaks under Kafka. Milestone 3 is where that gets paid for, which is
why subscribers here are already required to be idempotent by contract even
though nothing yet redelivers.
"""

from __future__ import annotations

from typing import Protocol

from app.core.logging import get_logger
from app.events.types import DomainEvent

logger = get_logger(__name__)


class EventSubscriber(Protocol):
    """Receives events. Must be idempotent.

    Idempotency is required *now*, before anything redelivers, because a
    subscriber written against exactly-once delivery is not something you can
    retrofit later by reading it — the assumption is invisible in the code and
    shows up as duplicated rows in production.
    """

    @property
    def name(self) -> str:
        """Identifies the subscriber in logs. Becomes the Kafka consumer group."""
        ...

    def handle(self, event: DomainEvent) -> None:
        """Process one event, or raise.

        Raising is allowed and is not fatal to the producer: the bus isolates
        subscribers from each other, because one broken consumer must not stop
        indexing from finishing.
        """
        ...


class EventPublisher(Protocol):
    """Publishes an event to whoever is listening."""

    def publish(self, event: DomainEvent) -> None: ...


class InProcessEventBus:
    """Synchronous fan-out to registered subscribers.

    Order is the registration order, and it is not a dependency: subscribers
    must not rely on another having run first. That constraint is what makes
    them separable into independent consumer groups later without changing
    their code.
    """

    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers.append(subscriber)

    @property
    def subscribers(self) -> tuple[EventSubscriber, ...]:
        return tuple(self._subscribers)

    def publish(self, event: DomainEvent) -> None:
        for subscriber in self._subscribers:
            try:
                subscriber.handle(event)
            except Exception:
                # Isolated deliberately. A trace recorder failing must not fail
                # the indexing run it is describing -- the work is the point and
                # the trace is the commentary. Logged with exception info so a
                # silently broken consumer is still discoverable.
                logger.exception(
                    "event_subscriber_failed",
                    subscriber=subscriber.name,
                    event_type=event.event_type,
                    key=event.key,
                )


class NullEventPublisher:
    """Discards everything. For paths that should emit nothing, and for tests
    that assert a code path publishes rather than what a subscriber did."""

    def publish(self, event: DomainEvent) -> None:
        return None
