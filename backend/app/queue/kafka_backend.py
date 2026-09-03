"""Kafka as the indexing work queue, behind the ADR-003 interface.

This exists to show that `JobQueue` was a real seam rather than a claimed one:
the API calls `enqueue_index_repository` and never learns which backend took it.

**It is not the recommended default, and that is deliberate.** ADR-004 scopes
Kafka to *events* — facts about what happened — and a work queue is a different
thing. Redis/RQ remains the default (`QUEUE_BACKEND=rq`), and the honest reason
to prefer it here is that RQ gives per-job retry, failure registries and
visibility that Kafka does not, while Kafka's advantages — replay, independent
consumer groups — are worth little for work dispatch where exactly one consumer
should act on each message.

What Kafka does bring, and the reason this is more than a demonstration: the
durable log means an enqueue survives the queue being down. With Redis stopped,
`enqueue` fails and the request fails with it; with Kafka the record persists
and a worker started later still picks it up.

**Agent runs are not carried here.** ADR-004 says agent reasoning is a state
machine rather than a stream, so `enqueue_agent_run` delegates to RQ. A backend
that quietly changed the transport for a workload the ADR excluded would be
doing the opposite of what the interface is for.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Versioned like the event topic. A queue message is a contract too, and the
# day its shape changes a new topic is cheaper than a migration.
JOBS_TOPIC: Final = "aisep.jobs.indexing.v1"
CONSUMER_GROUP: Final = "indexing-workers"


class KafkaJobQueue:
    """Dispatches indexing work through a Kafka topic."""

    def __init__(self, settings: Settings | None = None) -> None:
        from confluent_kafka import Producer

        self._settings = settings or get_settings()
        self._producer = Producer(
            {
                "bootstrap.servers": self._settings.kafka_bootstrap_servers,
                "enable.idempotence": True,
                "acks": "all",
                "client.id": "aisep-job-producer",
            }
        )

    def enqueue_index_repository(self, job_id: uuid.UUID) -> None:
        """Publish the job id, and nothing else.

        The same rule the RQ backend follows: the worker reloads everything
        from the database, so no payload can go stale between enqueue and
        execution and no access token is ever written to a log that is, by
        design, durable and replayable.

        Keyed by job id so retries of the same job stay on one partition and
        cannot be processed out of order relative to each other.
        """
        self._producer.produce(
            topic=JOBS_TOPIC,
            key=str(job_id).encode("utf-8"),
            value=json.dumps({"job_id": str(job_id)}).encode("utf-8"),
        )
        remaining = self._producer.flush(self._settings.kafka_flush_timeout_seconds)
        if remaining:
            # Raised, unlike a lost event. An event is commentary and losing it
            # costs a trace; a lost job means the user's index silently never
            # happens, and they are left watching a spinner.
            raise RuntimeError(
                f"Could not confirm the indexing job reached Kafka ({remaining} pending)"
            )
        logger.info("job_enqueued", job_id=str(job_id), topic=JOBS_TOPIC)

    def enqueue_agent_run(self, run_id: uuid.UUID, *, allow_tests: bool) -> None:
        """Delegate to RQ. ADR-004 keeps agent runs off Kafka."""
        from app.queue.rq_backend import get_rq_queue

        get_rq_queue().enqueue_agent_run(run_id, allow_tests=allow_tests)


def consume_jobs(
    *,
    settings: Settings | None = None,
    max_jobs: int | None = None,
    until_drained: bool = False,
    poll_timeout: float = 1.0,
) -> int:
    """Run an indexing worker against the jobs topic.

    Offsets are committed **after** the job finishes, so a worker that dies
    mid-index redelivers rather than losing the job. That means a job can run
    twice, which indexing already tolerates: it is incremental and content
    addressed, so a second run over unchanged files does almost nothing. That
    property is what makes at-least-once acceptable here rather than merely
    survivable.
    """
    from confluent_kafka import Consumer, KafkaError

    from app.workers.ingestion import run_index_job

    resolved = settings or get_settings()
    consumer = Consumer(
        {
            "bootstrap.servers": resolved.kafka_bootstrap_servers,
            "group.id": CONSUMER_GROUP,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            # Indexing takes minutes. Without this the broker assumes a worker
            # mid-index has died and rebalances its partition away, handing the
            # same job to someone else while the first is still working.
            "max.poll.interval.ms": 30 * 60 * 1000,
        }
    )
    consumer.subscribe([JOBS_TOPIC])
    handled = 0
    idle = 0

    try:
        while max_jobs is None or handled < max_jobs:
            message = consumer.poll(poll_timeout)
            if message is None:
                if not until_drained:
                    continue
                idle += 1
                if idle >= 3:
                    break
                continue
            error = message.error()
            if error is not None:
                if error.code() != KafkaError._PARTITION_EOF:
                    logger.warning("kafka_job_consume_error", error=str(error))
                continue

            raw = message.value()
            if raw is None:
                # A tombstone, or a producer that sent a null body. Nothing to
                # run, and leaving it uncommitted would block the partition.
                consumer.commit(message=message, asynchronous=False)
                continue

            idle = 0
            payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
            try:
                run_index_job(str(payload["job_id"]))
            except Exception:
                # The job row already records the failure; the worker's own
                # error handling ran. Committing past it stops one poisonous
                # job blocking every job behind it on the partition.
                logger.exception("kafka_job_failed", job_id=payload.get("job_id"))
            consumer.commit(message=message, asynchronous=False)
            handled += 1
    finally:
        consumer.close()

    return handled
