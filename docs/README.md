# Documentation index

| Document | Contents |
| --- | --- |
| [hld.md](hld.md) | High-level design: components, data flows, boundaries |
| [lld.md](lld.md) | Low-level design: module layout and responsibilities |
| [database.md](database.md) | Schema, keys, indexes, migrations |
| [api.md](api.md) | REST endpoints, error envelope, status codes |
| [rag.md](rag.md) | Ingestion, chunking, hybrid retrieval, reranking |
| [agents.md](agents.md) | Agent loop, state, tools, permissions |
| [security.md](security.md) | Trust boundaries, secrets, sandbox, prompt injection |
| [deployment.md](deployment.md) | Local development now; Stage 3 cloud target |
| [adr/](adr/) | Architecture decision records |
| [PRD.pdf.pdf](PRD.pdf.pdf) | Original product requirements document |

## Implementation status

This table reflects what is **actually built and verified**, not what is
planned. Documents describing later milestones mark unbuilt sections as
`Planned`. Nothing here is claimed as working until it has been run.

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Walking skeleton: compose, config, logging, migrations, `/health`, UI | ✅ **Done — verified** |
| 2 | GitHub OAuth + repository listing | ⬜ Not started |
| 3 | Ingestion: discovery, filtering, tree-sitter parsing, chunking | ⬜ Not started |
| 4 | Embeddings + pgvector storage, incremental re-indexing | ⬜ Not started |
| 5 | Hybrid retrieval (vector + full-text), merge, dedupe | ⬜ Not started |
| 6 | Evaluation harness, labelled benchmark, Recall@K / Precision@K | ⬜ Not started |
| 7 | Reranking + chat answers with citations | ⬜ Not started |
| 8 | RAG inspector UI | ⬜ Not started |
| 9 | Agent loop, tools, Docker sandbox, patch proposal + diff viewer | ⬜ Not started |

### Milestone 1 — what was verified

Not merely "the code exists":

- `docker compose up -d` brings up Postgres 16 + pgvector and Redis 7, both
  reporting healthy.
- `alembic upgrade head` creates the `vector` extension plus `users` and
  `repositories`; `alembic check` reports no model/schema drift.
- `GET /health` returns real measured connectivity — confirmed by stopping the
  Redis container and observing the response change to HTTP 503 `degraded`
  with `redis: ConnectionError`, then recover after restart.
- The Next.js page server-renders those real values, including per-dependency
  latency, and shows the degraded state when a dependency is genuinely down.
- Quality gate: `ruff` clean, `mypy --strict` clean on 17 files, 13 tests
  passing (unit + integration), `tsc --noEmit` clean, `eslint` clean,
  `next build` succeeding.
