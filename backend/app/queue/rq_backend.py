"""Redis + RQ implementation of the job queue (ADR-003)."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Final

from redis import Redis
from rq import Queue

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

QUEUE_NAME: Final = "ingestion"
# Indexing a large repository is minutes of parsing, not seconds. The default
# 180s would kill a legitimate run partway through.
JOB_TIMEOUT_SECONDS: Final = 60 * 30
# The worker entrypoint, named as a string so the API process never imports the
# ingestion stack -- it would otherwise pull tree-sitter into the web server for
# no reason.
WORKER_ENTRYPOINT: Final = "app.workers.ingestion.run_index_job"
AGENT_ENTRYPOINT: Final = "app.workers.agent.run_agent_job"


class RQJobQueue:
    """Enqueues work onto Redis."""

    def __init__(self, queue: Queue) -> None:
        self._queue = queue

    def enqueue_index_repository(self, job_id: uuid.UUID) -> None:
        self._queue.enqueue(
            WORKER_ENTRYPOINT,
            str(job_id),
            job_timeout=JOB_TIMEOUT_SECONDS,
            # Keep finished and failed jobs briefly for debugging; the durable
            # record lives in Postgres, so this is only ever a convenience.
            result_ttl=3600,
            failure_ttl=86400,
        )
        logger.info("job_enqueued", job_id=str(job_id), queue=QUEUE_NAME)

    def enqueue_agent_run(self, run_id: uuid.UUID, *, allow_tests: bool) -> None:
        self._queue.enqueue(
            AGENT_ENTRYPOINT,
            str(run_id),
            allow_tests,
            job_timeout=JOB_TIMEOUT_SECONDS,
            result_ttl=3600,
            failure_ttl=86400,
        )
        logger.info("agent_run_enqueued", run_id=str(run_id), allow_tests=allow_tests)


@lru_cache(maxsize=1)
def get_queue() -> RQJobQueue:
    """Return the process-wide queue handle."""
    redis = Redis.from_url(str(get_settings().redis_url))
    return RQJobQueue(Queue(QUEUE_NAME, connection=redis))
