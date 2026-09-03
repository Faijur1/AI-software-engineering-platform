"""Kafka producer and consumer for indexing events (ADR-004).

The broker that milestone 1's in-process bus was standing in for. Nothing in
`types.py` or `recorder.py` changed to accommodate it, which is the property the
seam existed to provide.

**Producer settings are the delivery guarantee**, so they are set explicitly
rather than left at defaults:

`enable.idempotence=true` stops a producer *retry* from writing the same event
twice. It does not make the system exactly-once — a consumer can still be
handed the same event again after a rebalance or a crash before commit — but it
removes the one duplicate source the producer itself controls.

`acks=all` waits for the replicas to acknowledge. On a single-broker
development cluster that means one replica and buys little; it is set anyway
because the value that matters is the one a real cluster needs, and a default
that silently means "acknowledged by nobody" is the wrong thing to inherit.

**Consumer settings are where at-least-once actually lives.** Auto-commit is
off. Offsets advance only after a handler returns, so a crash mid-handle
redelivers rather than skips. Losing an event is unrecoverable; seeing one twice
is a problem the handler can solve, which is why subscribers were required to be
idempotent before any of this existed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.events.publisher import EventSubscriber
from app.events.types import INDEXING_TOPIC, DomainEvent

logger = get_logger(__name__)


def _producer_config(settings: Settings) -> dict[str, Any]:
    return {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "enable.idempotence": True,
        "acks": "all",
        # Retry rather than drop. With idempotence on, a retry cannot duplicate.
        "retries": 5,
        "client.id": "aisep-indexing-producer",
    }


def _consumer_config(settings: Settings, group_id: str, *, from_start: bool) -> dict[str, Any]:
    return {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": group_id,
        # The whole point: offsets move when the handler says so, not when the
        # message was handed over.
        "enable.auto.commit": False,
        # "earliest" so a brand-new consumer group replays the log from the
        # beginning rather than silently starting at "now" and appearing to
        # work while having missed everything already written.
        "auto.offset.reset": "earliest" if from_start else "latest",
    }


class KafkaEventPublisher:
    """Publishes indexing events to the durable log."""

    def __init__(self, settings: Settings | None = None) -> None:
        from confluent_kafka import Producer

        self._settings = settings or get_settings()
        self._producer = Producer(_producer_config(self._settings))

    def publish(self, event: DomainEvent) -> None:
        """Send one event, keyed by job id so a run stays on one partition.

        Flushed on every call. Six events per indexing run makes the cost
        irrelevant, and the alternative -- buffering -- means a crash loses
        exactly the trace of the run that crashed, which is when a trace is
        most worth having.
        """
        self._producer.produce(
            topic=INDEXING_TOPIC,
            key=event.key.encode("utf-8"),
            value=json.dumps(event.to_dict()).encode("utf-8"),
            on_delivery=self._on_delivery,
        )
        remaining = self._producer.flush(self._settings.kafka_flush_timeout_seconds)
        if remaining:
            # Reported, not raised. The indexing run is the point; losing the
            # commentary on it must not fail the work being described.
            logger.warning(
                "kafka_publish_not_confirmed",
                event_type=event.event_type,
                key=event.key,
                unflushed=remaining,
            )

    @staticmethod
    def _on_delivery(error: Any, message: Any) -> None:
        if error is not None:
            logger.warning("kafka_delivery_failed", error=str(error))


def consume(
    subscriber: EventSubscriber,
    *,
    settings: Settings | None = None,
    group_id: str | None = None,
    from_start: bool = True,
    max_messages: int | None = None,
    until_drained: bool = False,
    poll_timeout: float = 1.0,
    join_timeout: float = 30.0,
) -> int:
    """Run one consumer group, handing each event to ``subscriber``.

    Returns the number of events handled, which is what makes this testable.

    Stopping is an explicit choice rather than something inferred from the
    arguments, because conflating the two is a mistake that costs a hung
    process: ``until_drained=True`` reads to the end of the log and returns,
    which is what a test or a replay wants, while the default runs forever
    waiting for the next run, which is what a worker wants. ``max_messages``
    caps either.

    The commit happens *after* the handler returns. That ordering is the entire
    delivery guarantee: crash before it and the event is redelivered, which is
    recoverable; commit first and the event is lost, which is not.
    """
    from confluent_kafka import Consumer

    resolved = settings or get_settings()
    # The group defaults to the subscriber's own name, so the offsets and the
    # code that advances them cannot drift apart. It is overridable because a
    # test needs its own group: sharing one means a test consumes whatever
    # earlier tests left behind and asserts on a backlog rather than its own
    # events.
    consumer = Consumer(
        _consumer_config(resolved, group_id or subscriber.name, from_start=from_start)
    )
    consumer.subscribe([INDEXING_TOPIC])
    handled = 0

    try:
        for message in _messages(
            consumer,
            poll_timeout=poll_timeout,
            limit=max_messages,
            until_drained=until_drained,
            join_timeout=join_timeout,
        ):
            try:
                event = DomainEvent.from_dict(json.loads(message.value().decode("utf-8")))
            except (ValueError, KeyError, TypeError):
                # A message that cannot be decoded is committed past rather than
                # retried forever. It is logged in full so it can be recovered
                # from the log by offset; blocking the partition on one bad
                # record would stop every good one behind it.
                logger.exception(
                    "kafka_event_undecodable",
                    offset=message.offset(),
                    partition=message.partition(),
                )
                consumer.commit(message=message, asynchronous=False)
                continue

            subscriber.handle(event)
            consumer.commit(message=message, asynchronous=False)
            handled += 1
    finally:
        consumer.close()

    return handled


def _messages(
    consumer: Any,
    *,
    poll_timeout: float,
    limit: int | None,
    until_drained: bool,
    join_timeout: float,
) -> Iterator[Any]:
    """Yield messages until the limit is reached, the log drains, or forever.

    Two silences mean different things, and conflating them was a real bug
    here. Before the first message, silence usually means the consumer has not
    finished joining the group and being assigned a partition -- a handshake
    that takes seconds, during which an impatient loop concludes the topic is
    empty and returns nothing. After the first message, silence means the log
    is drained.

    So the wait before the first message is generous and bounded by
    ``join_timeout``; afterwards a few idle polls are enough to call it drained.
    An unbounded run never gives up at all, because a worker should sit waiting
    for the next indexing run rather than exiting when there is nothing to do.
    """
    from confluent_kafka import KafkaError

    seen = 0
    idle_polls = 0
    deadline = time.monotonic() + join_timeout

    while limit is None or seen < limit:
        message = consumer.poll(poll_timeout)
        if message is None:
            if not until_drained:
                continue  # a worker waits indefinitely for the next run
            if seen == 0:
                if time.monotonic() >= deadline:
                    logger.warning("kafka_no_assignment", waited_seconds=join_timeout)
                    return
                continue
            idle_polls += 1
            if idle_polls >= 3:
                return
            continue
        if message.error():
            if message.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.warning("kafka_consume_error", error=str(message.error()))
            continue
        idle_polls = 0
        seen += 1
        yield message
