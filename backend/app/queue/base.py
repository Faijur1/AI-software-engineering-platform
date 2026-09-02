from __future__ import annotations

import uuid
from typing import Protocol


class JobQueue(Protocol):
    """Hands a unit of work to a background worker.

    A Protocol rather than an ABC: the API depends on the shape, not on a base
    class, so a test double needs no inheritance and the Kafka implementation
    in Stage 3 need not import anything from here.
    """

    def enqueue_index_repository(self, job_id: uuid.UUID) -> None:
        """Queue an indexing run for an already-persisted ``jobs`` row.

        Takes only the job id. The worker reloads everything else from the
        database, so no task payload can go stale between enqueue and execution,
        and no access token is ever written into the queue.
        """
        ...

    def enqueue_agent_run(self, run_id: uuid.UUID, *, allow_tests: bool) -> None:
        """Queue an agent run for an already-persisted ``agent_runs`` row.

        ``allow_tests`` travels with the job rather than being read from the
        run: it is a permission grant made at request time, and re-deriving it
        later would risk widening it.
        """
        ...
