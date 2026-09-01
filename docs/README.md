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
| 3 | Ingestion: discovery, filtering, tree-sitter parsing, chunking | ✅ **Done — verified** |
| 4 | Embeddings + pgvector storage, incremental re-indexing | ✅ **Done — verified** |
| 5 | Hybrid retrieval (vector + full-text), merge, dedupe | ✅ **Done — verified** |
| 6 | Evaluation harness, labelled benchmark, Recall@K / Precision@K | ✅ **Done — verified** |
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

### Milestone 3 — what was verified

Against the **real** github.com, not only against fixtures:

- A queued index of a real repository ran end to end through Redis + RQ and a
  separate worker process: `POST /repositories/{id}/index` returned 202, the
  job progressed through its stages, and it finished `succeeded`.
- 81 files and 296 chunks were written, at commit `c2dc171` — the actual
  current head of `origin/main`, resolved through the GitHub API rather than
  assumed.
- Symbols and line ranges are real: `Settings.is_production` as a `method` at
  L76-78, `session_scope` as a `function` at L37-52, and so on, each pointing
  at the code it names.
- Chunk kinds across that run: 145 `function`, 64 `block`, 45 `fallback`,
  37 `class`, 3 `fragment`, 2 `method`.
- **Security, asserted against the indexed data:** zero `.env` files indexed,
  zero chunks containing a real credential, zero `node_modules` files indexed.
- **Incremental re-indexing:** re-queueing the same repository reported all
  files unchanged and reused the identical chunk rows — the property that will
  make re-embedding skippable in milestone 4.
- The UI renders live progress from the backend, with no simulated bar: a
  repository already indexed shows "Re-index" and its indexed timestamp.

Also asserted by the suite, with fixtures rather than the network: secrets
excluded ahead of every other rule, symlinks not followed, binary content
rejected by a strict decode even under a `.py` name, unparseable source
degrading instead of raising, deleted files removed from the index, chunks
cascading when a repository is disconnected, and one user unable to read
another's job.

- Quality gate: `ruff` clean, `mypy --strict` clean on 44 files, 139 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.

**Not built yet, by design:** embeddings and the `embedding` column arrive in
milestone 4. Chunks are stored with their text and `content_tsv` only, so
nothing here claims a working vector search.

### Milestone 4 — what was verified

Against the **real** embedding model, not only against fixtures:

- A full index of a real repository embedded **457 of 457 chunks** (100%) with
  `nomic-embed-text`, at 768 dimensions, written into pgvector and read back at
  full width.
- Semantic search over those vectors returns genuinely relevant code. "How are
  GitHub access tokens encrypted before storage" ranks `encrypt_token` and the
  `security.py` module docstring top; "how are source files split into chunks"
  ranks `chunk_source` top; "what excludes secret files from indexing" ranks
  `is_secret_path` top.
- **Incremental re-indexing measured:** the first run took 149 s, re-running
  with nothing changed took **8 s**, and all 457 vectors survived — unchanged
  files keep their chunk rows, so their embeddings are never recomputed.
- The live-model tests (`pytest -m llm`) assert the properties the schema
  depends on: vector width matches `EMBEDDING_DIMENSIONS`, a batch returns
  vectors in input order, and semantically related code embeds closer than
  unrelated code.

Also asserted with fixtures: a short or ragged response is rejected rather than
misaligning vectors with chunks, a model of the wrong width fails with an
actionable message, changing the model re-embeds everything, an outage partway
leaves the pending chunks retryable, embedding one repository never touches
another's chunks, and a cosine-ranked query returns the matching chunk first.

- Quality gate: `ruff` clean, `mypy --strict` clean on 48 files, 166 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.

#### An honest limitation found while verifying

Vector search alone is **not** reliably good yet. The query "where is the OAuth
callback handled" ranked the README and a sign-in button above
`github_callback` in `routes/auth.py` — the exact function that handles it.

This is the failure mode ADR-005 and [the hybrid retrieval design](rag.md#why-hybrid)
predict: embeddings are weak on exact identifiers, where keyword search is
strong. It is recorded here rather than left for a user to discover, and it is
precisely what milestone 5 (hybrid retrieval) and milestone 6 (a labelled
benchmark with Recall@K) exist to fix and to measure. **No retrieval-quality
claim is being made at this milestone** — only that vectors are produced,
stored, and searchable.

### Milestone 5 — what was verified

Against the **real** index of this repository:

- **The milestone-4 failure is fixed.** "Where is the OAuth callback handled"
  ranked the README first under vector-only search and never surfaced
  `github_callback`. Under hybrid retrieval, `routes/auth.py:91
  github_callback` ranks **first** — found by vector at rank 7 and by keyword
  at rank 1, and lifted by appearing in both.
- Fusion demotes a weak keyword match the vector side disagrees with: for the
  query `github_callback`, keyword search ranked `.env.example` first (a short
  file, which `ts_rank` favours); hybrid put the actual handler first and
  `.env.example` fourth.
- The live endpoint returns per-retriever scores and ranks, reports candidate
  counts from each side, and reports `reranker_is_passthrough: true` with
  `rerank_score: null` on every result.

Asserted by the suite: RRF matches the published formula exactly; a chunk found
by both outranks one found by either; raw scores a thousand times larger do not
influence the merge; ties break deterministically; retrieval never crosses
repository boundaries; a stopword-only query degrades to vector-only *and says
so*; a failed embedder degrades to keyword-only *and says so*; unembedded
chunks are excluded rather than treated as distant; and the passthrough
reranker neither reorders nor fabricates a score.

- Quality gate: `ruff` clean, `mypy --strict` clean on 57 files, 194 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.

#### Two honest limitations

**Hybrid is not uniformly better.** For "how do I run the ingestion worker",
`workers/run_worker.py` was vector rank 1 — the right answer — but keyword
search did not find it, so RRF placed it **fifth**, behind four chunks both
retrievers agreed on. Rewarding agreement is usually right and is the whole
point of fusion, but it demotes a correct result that only one retriever found.
This is a real trade-off of rank fusion, not a bug, and it is exactly the kind
of thing the milestone-6 benchmark exists to quantify.

**Still no retrieval-quality claim.** These are individual queries chosen by
hand, which is anecdote, not measurement. Recall@K and Precision@K against a
labelled benchmark arrive in milestone 6 — and that benchmark must be built
against the current passthrough reranker so the real reranker's contribution in
milestone 7 can be demonstrated rather than asserted.

### Milestone 6 — what was verified

26 labelled questions, run against the real index of this repository at commit
`1a797b7` (604 embedded chunks), with the reranker inert. **These are the
pre-reranking baseline.**

| configuration | R@1 | R@3 | R@5 | R@10 | P@5 | MRR |
| --- | --- | --- | --- | --- | --- | --- |
| vector only | 0.500 | 0.654 | **0.731** | 0.731 | 0.169 | **0.634** |
| keyword only | 0.096 | 0.385 | 0.519 | 0.596 | 0.131 | 0.279 |
| **hybrid** | 0.442 | **0.731** | **0.744** | **0.769** | **0.185** | 0.625 |

Recall@5 by question style:

| configuration | conceptual | identifier | mixed |
| --- | --- | --- | --- |
| vector only | 0.450 | **0.833** | **1.000** |
| keyword only | 0.400 | 0.667 | 0.429 |
| **hybrid** | **0.533** | 0.778 | **1.000** |

#### Four findings

**1. Hybrid wins on recall past rank 1, and loses at rank 1.** It beats both
baselines at R@3, R@5, R@10 and every precision cutoff — but vector-only is
better at R@1 (0.500 vs 0.442) and marginally better on MRR (0.634 vs 0.625).
This is the trade-off predicted at milestone 5 and now measured: rank fusion
rewards agreement, which demotes a correct top result that only one retriever
found.

**2. The design's assumption about identifiers was wrong.**
[`rag.md`](rag.md) argued that vector search is unreliable for exact
identifiers and keyword search covers that gap. On this corpus with
`nomic-embed-text`, vector-only scored **0.833** recall@5 on identifier
questions against keyword-only's **0.667**. The reason hybrid helps here is
recall breadth, not the identifier weakness the design predicted. The
assumption has been corrected in the document.

**3. Keyword search is the weaker retriever throughout** — 0.279 MRR against
0.634. It still earns its place, because it recovers cases vector search misses
entirely, but "hybrid" here means "vector, topped up by keyword", not two equal
contributors.

**4. Tests and documentation crowd out implementation.** All five questions
hybrid failed at K=5 failed the same way. Asking for `OllamaEmbedder` returns
`test_ollama_embedder.py` and `test_ollama_live.py`; conceptual security
questions return `docs/security.md` and `docs/lld.md`. The prose *about* the
code out-competes the code. This is the single most actionable finding, and the
most likely source of a real gain in milestone 7.

#### A bug the benchmark caught immediately

Building the harness exposed a genuine defect in milestone 5's keyword search.
The relaxation step round-tripped the rendered tsquery through `to_tsquery`,
which **re-stems** lexemes: `something_else` renders as `'someth' <-> 'els'`
and came back as `'someth' <-> 'el'`, matching nothing. It silently affected
only terms whose stem stems again, which is why nothing noticed. Fixed by
casting `text::tsquery`, which parses lexemes verbatim, with a regression test.

#### Two limitations of the benchmark itself

**26 questions is small.** A difference of 0.04 in recall is one question. The
gaps between hybrid and vector-only at R@1 and MRR are within that margin and
should not be treated as established.

**The benchmark has now been used for tuning, so it is no longer fully
held out.** After measuring, keyword search was changed to top up its candidate
list from the relaxed query rather than only falling back when the strict query
found nothing. That change is independently justified — returning 3 candidates
when 50 were requested wastes the budget fusion exists to use — but it was made
with the numbers visible. Tuning was stopped there deliberately. **Future
tuning claims need held-out questions the tuner has not seen.**

- Quality gate: `ruff` clean, `mypy --strict` clean on 59 files, 216 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.
