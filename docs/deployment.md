# Deployment

## Local development (current)

Stateful dependencies run in Docker; the application runs on the host for fast
iteration.

```
docker compose up -d      # postgres + pgvector, redis
backend:  uvicorn app.main:app --reload      (port 8000)
frontend: npm run dev                        (port 3000)
```

Prerequisites and exact commands are in the root [README](../README.md).

### Verified environment

| Component | Version |
| --- | --- |
| Python | 3.11.9 (pinned — see below) |
| Node.js | 24.20.0 |
| Docker | 29.7.2, Compose v5.4.0 |
| PostgreSQL | 16 (`pgvector/pgvector:pg16`) |
| Redis | 7-alpine |
| Ollama | 0.33.2 — `qwen2.5-coder:7b`, `nomic-embed-text` |

**Python 3.11 is pinned deliberately.** Python 3.14 is the default interpreter
on the development machine, but tree-sitter grammars and parts of the scientific
stack used in later milestones lack reliable prebuilt Windows wheels for it,
which would force source builds. The backend venv is created with `py -3.11`.

## Environments

`development`, `test`, `production`, selected by `APP_ENV`. All configuration
comes from the environment through one validated settings object. In production,
API docs are disabled and logs are JSON.

## CI

`.github/workflows/ci.yml`, on pull requests and on pushes to `main`. Three
jobs run in parallel; within a job the steps are ordered cheapest-first, so an
obvious failure is reported in seconds rather than after the slow tiers.

| Job | Steps |
| --- | --- |
| Backend | `ruff` -> `mypy` -> unit tests -> `alembic upgrade head` -> `alembic check` -> integration tests -> kafka tests |
| Sandbox | build `aisep-sandbox:latest` -> tests marked `sandbox` |
| Frontend | `eslint` -> `tsc --noEmit` -> `next build` |

`alembic check` is in the pipeline specifically to fail the build when models
drift from migrations -- a divergence no test can see, which otherwise surfaces
in production as a missing column.

The backend job runs Postgres, Redis and Kafka as service containers, using the
same images as `docker-compose.yml`. `pgvector/pgvector:pg16` rather than plain
`postgres`: the extension is not in the stock image, and the first migration
would fail without it. Kafka runs in KRaft mode with a single broker, so the
`kafka` tier is genuinely exercised rather than merely written -- tests CI never
runs are tests CI does not cover, which is a lesson this pipeline learned the
hard way when those tests briefly ran in the unit job against no broker.

The five tiers partition the suite exactly, and
`tests/unit/test_ci_tiers.py` asserts that: a marker declared without being
added to a selector fails immediately rather than silently running its tests in
a job that cannot serve them.

The sandbox job builds the image rather than pulling one. ADR-006 makes the
sandbox a security boundary, and its tests assert that the network is
unreachable and the host filesystem invisible, so they need the real image --
the base has no pytest, and `--network none` means it could never be installed
at run time.

CI holds no credentials. The suite supplies its own obviously-fake secrets
through an autouse fixture, so the workflow sets only the service URLs, and no
step ever reaches a live account.

Tests marked `llm` are excluded: they need a running Ollama with models pulled,
which is a developer-machine dependency rather than a CI one. That gap is real
and worth stating -- nothing in CI exercises a live model.

## Stage 3 cloud target (not built)

Kafka and the event log *are* built and run locally (ADR-004); what follows is
the managed-service target, which is not.

Not built. Introduced only once the AI core is proven, and each piece has an ADR
recording the problem it solves.

| Concern | Service | Rationale |
| --- | --- | --- |
| Orchestration | EKS | Autoscale bursty workers on backlog (ADR-005) |
| Events | MSK (Kafka) | Independent consumers of indexing events (ADR-004) |
| Database | RDS PostgreSQL + pgvector | Managed, same engine as development |
| Artifacts | S3 | Patch artifacts, test logs, evaluation datasets |
| Secrets | Secrets Manager | Replaces `.env` |
| Monitoring | CloudWatch | Latency, error rate, consumer lag, worker health |

CI/CD: GitHub Actions → test → build image → push → rolling deploy → health
check.

Deliberately excluded: microservice decomposition, and any service without a
demonstrated scaling or reliability problem.
