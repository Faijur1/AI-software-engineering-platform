# ADR-008 — Synchronous SQLAlchemy sessions across API and workers

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

The same models and data-access code are used by two runtimes: the FastAPI
application and the RQ workers. RQ workers are synchronous. FastAPI supports
both `async def` and `def` endpoints, running the latter in a threadpool.

Mixing an async session in the API with a sync session in the workers would mean
two session factories, two transaction patterns, and either duplicated
data-access functions or a sync/async bridge.

## Decision

Use synchronous SQLAlchemy sessions everywhere. Database-touching endpoints are
declared `def` so Starlette runs them in its threadpool.

Genuinely I/O-bound external calls that do not touch the database — LLM
streaming in particular — may still be `async`.

## Alternatives considered

- **Async SQLAlchemy everywhere.** Higher concurrency ceiling for the API, but
  RQ workers would need `asyncio.run()` wrappers around every job.
- **Async in the API, sync in workers.** Duplicates the data-access layer.

## Consequences

**Positive**

- One session pattern, one `session_scope()`, one set of transaction boundaries.
- Workers and request handlers share data-access code unchanged.
- Simpler to reason about and to test.

**Negative**

- API concurrency is bounded by the threadpool size (default 40) rather than by
  the event loop. This is not a constraint at Stage 1 scale, where the dominant
  latency is the LLM, not the database.
- Long-running DB work occupies a thread for its duration.

**Revisit if** threadpool saturation shows up as measured request queueing.
Because data access is confined to a service layer, the migration would be
contained rather than system-wide.
