# ADR-004 — Kafka introduced in Stage 3, for repository/indexing events only

- **Status:** Proposed (Stage 3)
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
