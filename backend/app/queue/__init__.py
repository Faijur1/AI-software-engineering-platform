"""Job queue interface.

Deliberately tiny, and the only speculative abstraction in Stage 1 (ADR-003).
It exists because the Kafka swap in Stage 3 is a known, planned change, and
keeping ``enqueue`` behind an interface means ingestion code never imports RQ.

Calling code sees ``enqueue()`` and nothing else about the backend.
"""

from __future__ import annotations

from app.queue.base import JobQueue
from app.queue.rq_backend import RQJobQueue, get_queue

__all__ = ["JobQueue", "RQJobQueue", "get_queue"]
