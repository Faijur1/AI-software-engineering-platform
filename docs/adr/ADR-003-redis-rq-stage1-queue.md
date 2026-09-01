# ADR-003 — Redis + RQ for the Stage 1 job queue, not Kafka

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

Repository indexing is long-running and must not block HTTP requests. It needs
to run in a background worker with visible progress and a retry path.

At Stage 1 this is one producer (the API, on user request) and one consumer
(the ingestion worker). There is no second independent consumer of indexing
events, and no requirement to replay a durable event log.

## Decision

Use Redis with RQ as the Stage 1 job queue, behind a small internal interface
(`enqueue_job()` / `process_job()`) that hides the backend from calling code.

## Alternatives considered

- **Kafka now.** Its real value is a durable, replayable log consumed by
  independent consumer groups. None of that is needed yet, and running it costs
  meaningful setup and operational time for no Stage 1 functional benefit.
- **Celery.** Comparable to RQ and more featureful, but heavier to configure.
  RQ is sufficient for the job shapes here.
- **Postgres-backed queue (`SELECT ... FOR UPDATE SKIP LOCKED`).** One fewer
  service to run, but Redis is already wanted for caching and rate limiting.

## Consequences

**Positive**

- Small operational surface; one container in development.
- Job state, progress, and failures map naturally onto the `jobs` table.

**Negative**

- Redis persistence is weaker than a durable log: a hard Redis loss can drop
  queued jobs. Acceptable because indexing is idempotent and re-runnable, and
  job records live in Postgres, so lost work is detectable and repeatable.
- No consumer groups or replay.

**Migration:** because enqueue/consume sit behind an interface, Stage 3 can
substitute a Kafka-backed implementation without changing ingestion logic. See
ADR-004. This is the only speculative abstraction accepted in Stage 1, and it is
accepted because the swap is known to be coming and the interface is ~20 lines.
