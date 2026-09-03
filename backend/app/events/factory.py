"""Chooses the publisher named by configuration.

One function, so exactly one place decides where indexing events go. The
producer calls ``get_event_publisher()`` and never learns which backend it got,
which is what allowed Kafka to arrive without ``types.py`` or ``recorder.py``
changing at all.

Not cached: a publisher is a thin object, ``get_settings`` is already cached,
and caching here would make a settings change invisible to a test.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.publisher import EventPublisher, InProcessEventBus
from app.events.recorder import TraceRecorder

logger = get_logger(__name__)


def get_event_publisher() -> EventPublisher:
    """Return the configured publisher.

    Takes no run-specific argument. Under "inprocess" the recorder is wired in
    here because there is no separate process to do it; under "kafka" the
    recorder is its own consumer group and this producer never learns it
    exists. Neither needs a trace id, because the event carries one.
    """
    settings = get_settings()

    if settings.event_backend == "kafka":
        from app.events.kafka import KafkaEventPublisher

        return KafkaEventPublisher(settings)

    bus = InProcessEventBus()
    bus.subscribe(TraceRecorder())
    return bus
