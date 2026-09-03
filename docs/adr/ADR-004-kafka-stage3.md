# ADR-004 — Kafka introduced in Stage 3, for repository/indexing events only

- **Status:** Proposed. **Adoption trigger met on 2026-09-04** — a second
  consumer of indexing events now exists; the broker itself is not yet built.
- **Date:** 2026-09-01

## Context

By Stage 3, a repository update is expected to have several genuinely
independent consumers: the embedding worker, analytics, notifications, and
re-indexing triggers. Each needs to consume the same events at its own pace,
and to recover by replaying them after a failure.

That is the problem a durable event log with consumer groups solves, and it is
not the problem a work queue solves.

## Decision

Introduce Kafka (Amazon MSK) in Stage 3, behind the same queue/event interface
introduced in Stage 1 (ADR-003), for repository and indexing events only.

Kafka is explicitly **not** used for:

- synchronous request/response paths — those stay HTTP + PostgreSQL;
- agent reasoning steps — those are a state machine, not a stream.

## Alternatives considered

- **Keep Redis/RQ.** Viable until multiple independent consumers actually exist;
  fan-out then has to be hand-rolled and is not replayable.
- **AWS SNS/SQS.** Fan-out without running Kafka, but no log replay.

## Consequences

**Positive**

- Independent consumer groups; a slow consumer cannot block the others.
- Replay makes backfills and recovery straightforward.

**Negative**

- Significant operational complexity: partitioning, consumer lag, rebalancing,
  and delivery semantics all become the team's problem.
- Consumers must be idempotent, since delivery is at-least-once.

**Trigger:** adopt only once a second real consumer of indexing events exists.
Topics are created when a consumer needs them, never speculatively.

---

## Trigger status

The trigger below said: *adopt only once a second real consumer of indexing
events exists.* It now does, and this section records how, because adopting
Kafka while that was still untrue would have been the decision this ADR was
written to prevent.

Indexing emits a closed vocabulary of seven facts (`app/events/types.py`), and a
**trace recorder** consumes them independently of the indexer. It holds no
indexing logic, it can fail without affecting a run, and it writes into the
`events` table the agent already uses — so an indexing run is now inspectable
through the same endpoint and replay UI. Before this it produced a progress bar
and, once finished, nothing anyone could examine.

Two consumers of the same events therefore exist: the indexer, which does the
work, and the recorder, which describes it.

**What is deliberately still missing.** The publisher is in-process
(`InProcessEventBus`): synchronous, no durability, no replay, and exactly-once
by construction. That last property is a trap, and it is why subscribers are
required to be idempotent *by contract* already — code written against
exactly-once delivery cannot be retrofitted later by reading it, because the
assumption is invisible and surfaces as duplicated rows.

Scope confirmed with the repository owner on 2026-09-04: local Kafka only, agent
runs stay on Redis/RQ as this ADR specifies, and **Kubernetes is not being
built** — see ADR-005.
