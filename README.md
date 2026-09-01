# AI Software Engineering Platform

An AI-powered developer platform that connects to a GitHub repository,
understands the codebase using retrieval-augmented generation, and uses an agent
to help with real software engineering tasks: answering questions about the
code, investigating bugs, running tests safely in a sandbox, and proposing
patches for human approval.

> ### Current status: Stage 1, milestone 4 of 9 — embeddings
>
> Infrastructure, GitHub OAuth, repository connection, ingestion (discovery,
> filtering, tree-sitter parsing, chunking) and **embeddings into pgvector are
> built and verified.** Hybrid retrieval, chat, the RAG inspector and the agent
> are not implemented yet.
>
> Vectors are produced, stored and searchable. **No claim is made yet about
> retrieval *quality*** — that is measured against a labelled benchmark in
> milestone 6, and a known weakness is recorded in
> [`docs/README.md`](docs/README.md).
>
> [`docs/README.md`](docs/README.md) tracks exactly what is built, what is
> verified, and what remains. Nothing is described as working until it has
> actually been run.

## Architecture

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript (strict), Tailwind |
| Backend | FastAPI, Python 3.11, SQLAlchemy 2, Pydantic v2 |
| Database | PostgreSQL 16 + pgvector |
| Queue | Redis + RQ (ADR-003) |
| Parsing | tree-sitter, AST-aware chunking (ADR-002) |
| Embeddings | Ollama — `nomic-embed-text`, 768-dim, pgvector + HNSW |
| LLM | Ollama — `qwen2.5-coder:7b` *(planned)* |
| Auth | GitHub OAuth, backend-owned; signed HttpOnly session cookie |
| Sandbox | Docker, isolated per run *(planned)* |

Design documents and architecture decision records live in [`docs/`](docs/).
Start with [`docs/hld.md`](docs/hld.md).

## Prerequisites

- **Python 3.11** — pinned; see [deployment notes](docs/deployment.md) for why
- **Node.js 20+**
- **Docker Desktop**, running
- **Ollama** running, with `nomic-embed-text` pulled — required for indexing:
  ```bash
  ollama pull nomic-embed-text     # embeddings, needed now
  ollama pull qwen2.5-coder:7b     # generation, needed from milestone 7
  ```

## Setup

### 1. Register a GitHub OAuth App

Sign-in needs your own OAuth App — at
<https://github.com/settings/developers> → **New OAuth App**:

| Field | Value |
| --- | --- |
| Homepage URL | `http://localhost:3000` |
| Authorization callback URL | `http://localhost:8000/auth/github/callback` |

The callback URL must match exactly: the backend owns the OAuth flow
([ADR-009](docs/adr/ADR-009-backend-owned-github-oauth.md)), so it is *not* the
NextAuth path. Copy the Client ID and generate a client secret; both go into
`.env` below.

### 2. Everything else

```bash
# Configuration
cp .env.example .env

# Fill in GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET from the OAuth App above,
# then generate the two secrets (both must be at least 32 characters):
openssl rand -base64 32       # -> NEXTAUTH_SECRET
openssl rand -base64 32       # -> TOKEN_ENCRYPTION_KEY

# Infrastructure
docker compose up -d          # postgres + pgvector, redis

# Backend
cd backend
py -3.11 -m venv .venv                    # Windows; use python3.11 elsewhere
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev

# Ingestion worker (in a third terminal) — required for indexing
cd backend
./.venv/Scripts/python.exe -m app.workers.run_worker
```

Without the worker running, indexing jobs queue and stay `queued`. Everything
else works. `GET /health` reports Ollama alongside Postgres and Redis, so a
missing model shows up there rather than only when a job fails.

Then open <http://localhost:3000>. The page shows live backend and dependency
status, and a **Sign in with GitHub** button. After signing in you land on
`/repositories`, where you can connect the repositories this platform may read,
then **Index** one — the progress shown is reported by the worker, not
simulated. Indexing parses the repository and embeds every chunk; the row then
shows real file, chunk and embedded counts.
<http://localhost:8000/docs> has the interactive API reference.

Without `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` the rest of the
application still runs; only sign-in is unavailable, and it says so rather than
failing silently.

To confirm the health reporting is real rather than cosmetic, stop a dependency
and reload the page:

```bash
docker compose stop redis     # page shows "Degraded" + ConnectionError
docker compose start redis    # recovers
```

## Security notes

- The GitHub access token is stored encrypted (`TOKEN_ENCRYPTION_KEY`), is never
  returned by any endpoint, and never reaches the browser.
- The session cookie is HttpOnly and signed; its algorithm and issuer are pinned
  at verification.
- Only read scopes are requested: `read:user user:email repo`. Stage 1 performs
  no GitHub writes.
- Rotating `TOKEN_ENCRYPTION_KEY` invalidates every stored token — users simply
  sign in again.
- Secret-bearing files (`.env`, private keys, credentials) are excluded from
  indexing before any other rule is applied, so they are never parsed, never
  stored, and can never be quoted back by the LLM. Repository archives are
  treated as untrusted input: extraction rejects path traversal and escaping
  symlinks, and both download and expanded size are bounded.

See [`docs/security.md`](docs/security.md) for the full model.

## Development

```bash
# Backend — from backend/
./.venv/Scripts/python.exe -m ruff check .        # lint
./.venv/Scripts/python.exe -m mypy app            # types (strict)
./.venv/Scripts/python.exe -m pytest              # all tests
./.venv/Scripts/python.exe -m pytest -m "not integration"   # no services needed
./.venv/Scripts/python.exe -m pytest -m llm                 # needs Ollama running
./.venv/Scripts/python.exe -m alembic check       # models vs migrations

# Frontend — from frontend/
npx tsc --noEmit
npm run lint
npm run build
```

Integration tests need `docker compose up -d` and a database migrated to head.

> **VS Code tip:** select `backend/.venv` as the Python interpreter, otherwise
> the editor reports imports as unresolved even though the venv is correct.

## Project layout

```
backend/     FastAPI application, workers, migrations, tests, evaluation
frontend/    Next.js application
docs/        HLD, LLD, API, database, RAG, agents, security, ADRs
docker-compose.yml
```

## Principles

- **No fake implementations.** No hardcoded AI answers, no fabricated metrics,
  no placeholder passed off as production-ready. Unfinished work is labelled.
- **Every technology solves a stated problem.** Kafka, Kubernetes and AWS are
  deferred to Stage 3, each with an ADR recording the problem it addresses.
- **Evaluate from day one.** Retrieval quality is measured against a labelled
  benchmark, and the suite is re-run whenever retrieval logic changes.
- **Security by default.** Repository content is untrusted data; AI-generated
  code executes only inside an isolated sandbox.
