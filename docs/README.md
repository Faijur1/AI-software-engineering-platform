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
| 2 | GitHub OAuth + repository listing | ✅ **Done — verified** |
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

### Milestone 2 — what was verified

- The full OAuth round trip runs end to end against the real database with
  GitHub mocked at the HTTP layer: a completed callback creates a `users` row,
  issues a working session, and `GET /auth/me` returns that user.
- The GitHub access token is encrypted at rest — the test reads the raw column
  and asserts the plaintext token does not appear in it, then decrypts it back.
  It is absent from every response body and from the session cookie.
- CSRF state is enforced: a callback with no state cookie, or a mismatched one,
  redirects with `auth_error=invalid_state` and issues no session.
- Session forgery is rejected: tampered signature, wrong secret, `alg: none`,
  wrong issuer, and expired tokens each raise rather than authenticate.
- Tenant isolation is asserted with two concurrently signed-in users: the second
  cannot list or delete the first's repository, and gets 404 rather than 403.
- Signing in again as a renamed GitHub account updates the existing user rather
  than creating a second one.
- Live check against the running server: `/auth/me` and `/repositories` return
  401 unauthenticated; `/auth/github/login` returns 307 to github.com with the
  state pinned in an HttpOnly cookie.
- Quality gate: `ruff` clean, `mypy --strict` clean on 26 files, 62 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.

#### Confirmed against the real github.com

Not only against the mock:

- A real browser sign-in created a `users` row with the correct GitHub id,
  login, name, email and avatar.
- The stored token is Fernet ciphertext (`gAAAAA…`, 140 chars) containing no
  trace of the 40-character `gho_…` plaintext. It decrypts, and the decrypted
  token successfully authenticates `GET /user` as that same account — so the
  encryption round trip is proven by use, not merely by symmetry.
- `GET /auth/me` returns that user and no token; without the cookie it is 401.
- `GET /repositories/github` returned the account's real repositories with
  correct language, visibility and `connected_id` values.
- Connect is idempotent (a second POST returns the same id, not a duplicate),
  disconnect returns 204 and then 404, and an unauthenticated POST is 401.
- A repository the token cannot see returns **404 `not_found`**, matching
  [api.md](api.md). This was found during that verification — it had returned
  502, and the integration test had asserted the implemented behaviour rather
  than the documented contract. Both were corrected.
