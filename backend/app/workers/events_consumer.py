"""Runs the trace recorder as its own Kafka consumer group (ADR-004).

    python -m app.workers.events_consumer

This is what milestone 1's second consumer becomes once there is a broker: a
separate process, with its own offsets, that can be stopped and restarted while
indexing continues. Stop it for an hour and indexing is unaffected; start it
again and it catches up from where it left off. Under the in-process bus,
stopping it was not a thing you could do -- it lived inside the producer.

The consumer group id is the subscriber's own name, so the group that owns the
offsets and the code that handles the events cannot drift apart.
"""

from __future__ import annotations

import sys

from app.core.logging import configure_logging, get_logger
from app.events.kafka import consume
from app.events.recorder import TraceRecorder
from app.events.topics import ensure_topics

logger = get_logger(__name__)


def main() -> int:
    configure_logging()

    ensure_topics()
    # The same recorder class the in-process bus uses. It is stateless and reads
    # the trace id from each event, so one implementation serves both backends
    # -- which is what makes them genuinely interchangeable rather than merely
    # similar.
    recorder = TraceRecorder()

    logger.info("event_consumer_started", group=recorder.name)
    try:
        handled = consume(recorder, from_start=True)
    except KeyboardInterrupt:
        logger.info("event_consumer_stopped")
        return 0
    logger.info("event_consumer_finished", handled=handled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
