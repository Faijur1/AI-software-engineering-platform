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

## CI (planned)

```
lint (ruff, eslint) -> typecheck (mypy --strict, tsc) -> unit tests
  -> integration tests (compose services) -> alembic check -> build
```

`alembic check` is in the pipeline specifically to fail the build when models
drift from migrations.

## Stage 3 cloud target (planned)

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
