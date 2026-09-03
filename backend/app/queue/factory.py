"""Chooses the job queue named by configuration (ADR-003).

The API calls ``get_queue()`` and never learns which backend took the work,
which is the property the interface exists to provide. Redis/RQ is the default:
ADR-004 scopes Kafka to events, and RQ gives per-job retry and failure
visibility that a log does not.

Not cached. A queue handle is thin, ``get_settings`` is already cached, and
caching here would make a settings change invisible to a test.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.queue.base import JobQueue


def get_queue() -> JobQueue:
    """Return the configured job queue."""
    settings = get_settings()

    if settings.queue_backend == "kafka":
        from app.queue.kafka_backend import KafkaJobQueue

        return KafkaJobQueue(settings)

    from app.queue.rq_backend import get_rq_queue

    return get_rq_queue()
