"""Worker process entrypoint.

    python -m app.workers.run_worker

Runs in its own process so that indexing -- minutes of CPU-bound parsing --
never competes with the API for request threads.
"""

from __future__ import annotations

import os

from redis import Redis
from rq import Queue, SimpleWorker, Worker

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.queue.rq_backend import QUEUE_NAME

logger = get_logger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    redis = Redis.from_url(str(settings.redis_url))
    queue = Queue(QUEUE_NAME, connection=redis)

    # RQ's default worker forks per job, which os.fork does not support on
    # Windows. SimpleWorker runs jobs in-process instead: no isolation between
    # jobs, but this is the local development path, and a crashing job takes
    # the worker down rather than corrupting another job.
    worker_class = Worker if hasattr(os, "fork") else SimpleWorker

    logger.info("worker_starting", queue=QUEUE_NAME, worker=worker_class.__name__)
    worker_class([queue], connection=redis).work(with_scheduler=False)


if __name__ == "__main__":
    main()
