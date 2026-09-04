"""Replay the indexing event log into the trace store.

    python -m app.workers.replay_events

Reports how many events were delivered and how many rows that actually wrote.
On a healthy system the second number is zero: every event has already been
recorded, and re-reading them changes nothing. That difference is the
demonstration -- an at-least-once log plus an idempotent consumer.
"""

from __future__ import annotations

import sys

from app.core.logging import configure_logging
from app.events.replay import replay
from app.events.topics import ensure_topics


def main() -> int:
    configure_logging()
    ensure_topics()

    result = replay()

    print(f"events delivered : {result.events_delivered}")
    print(f"rows before      : {result.rows_before}")
    print(f"rows after       : {result.rows_after}")
    print(f"rows written     : {result.rows_written}")
    if result.events_delivered and not result.rows_written:
        print("\nEvery event was already recorded: the consumer is idempotent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
