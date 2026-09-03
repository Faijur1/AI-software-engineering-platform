"""Topic creation, done explicitly rather than by auto-creation.

Kafka will create a topic on first use if the broker allows it, and that is a
bad way to get one: the partition count and replication factor come from broker
defaults nobody chose, and partition count cannot be reduced afterwards. A topic
is a schema-like decision and deserves to be written down.

**One partition, deliberately.** Ordering in Kafka is per partition, and the
trace of an indexing run only makes sense in order. Events are keyed by job id,
so more partitions would still keep each run ordered — but a single developer
indexing one repository at a time has no throughput problem to solve, and the
honest reason to add partitions is measured consumer lag, which ADR-005 records
as currently zero.

**Replication factor one, because there is one broker.** Stated explicitly so
that the day this runs on a real cluster, the line that needs changing is
visible rather than inherited from a default that quietly means no redundancy.
"""

from __future__ import annotations

from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.events.types import INDEXING_TOPIC

logger = get_logger(__name__)

PARTITIONS: Final = 1
REPLICATION_FACTOR: Final = 1
# Long enough that a replay is genuinely possible days later, short enough that
# a development machine does not fill its disk with indexing commentary.
RETENTION_MS: Final = 7 * 24 * 60 * 60 * 1000


def ensure_topics(settings: Settings | None = None, *, timeout: float = 20.0) -> list[str]:
    """Create the indexing topic if it is absent. Returns the topics present.

    Idempotent: an existing topic is left alone rather than reconfigured, since
    changing partitions on a live topic reorders nothing already written but
    does change where future keys land.
    """
    from confluent_kafka.admin import AdminClient, NewTopic

    resolved = settings or get_settings()
    admin = AdminClient({"bootstrap.servers": resolved.kafka_bootstrap_servers})

    existing = set(admin.list_topics(timeout=timeout).topics)
    if INDEXING_TOPIC in existing:
        return sorted(existing)

    topic = NewTopic(
        INDEXING_TOPIC,
        num_partitions=PARTITIONS,
        replication_factor=REPLICATION_FACTOR,
        config={"retention.ms": str(RETENTION_MS)},
    )
    for name, future in admin.create_topics([topic]).items():
        try:
            future.result(timeout=timeout)
            logger.info("kafka_topic_created", topic=name, partitions=PARTITIONS)
        except Exception as exc:
            # A concurrent creator is not an error; anything else is.
            if "already exists" in str(exc).lower():
                continue
            raise

    return sorted(set(admin.list_topics(timeout=timeout).topics))


def topic_exists(settings: Settings | None = None, *, timeout: float = 10.0) -> bool:
    from confluent_kafka.admin import AdminClient

    resolved = settings or get_settings()
    admin = AdminClient({"bootstrap.servers": resolved.kafka_bootstrap_servers})
    return INDEXING_TOPIC in admin.list_topics(timeout=timeout).topics


def describe(settings: Settings | None = None, *, timeout: float = 10.0) -> dict[str, Any]:
    """What the broker actually holds, for the CLI and for tests."""
    from confluent_kafka.admin import AdminClient

    resolved = settings or get_settings()
    admin = AdminClient({"bootstrap.servers": resolved.kafka_bootstrap_servers})
    metadata = admin.list_topics(timeout=timeout)
    topic = metadata.topics.get(INDEXING_TOPIC)
    if topic is None:
        return {"topic": INDEXING_TOPIC, "exists": False}
    return {
        "topic": INDEXING_TOPIC,
        "exists": True,
        "partitions": len(topic.partitions),
        "brokers": len(metadata.brokers),
    }
