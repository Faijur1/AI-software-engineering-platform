"""Domain events for indexing (ADR-004).

The vocabulary lives in ``types``, the seam in ``publisher``, and the second
consumer in ``recorder``. Kafka replaces the publisher in milestone 2 without
any of the other modules changing, which is the property the split exists for.
"""

from app.events.publisher import (
    EventPublisher,
    EventSubscriber,
    InProcessEventBus,
    NullEventPublisher,
)
from app.events.recorder import TraceRecorder
from app.events.types import DomainEvent

__all__ = [
    "DomainEvent",
    "EventPublisher",
    "EventSubscriber",
    "InProcessEventBus",
    "NullEventPublisher",
    "TraceRecorder",
]
