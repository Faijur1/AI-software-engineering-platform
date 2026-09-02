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
| 7 | Reranking + chat answers with citations | ⚠️ **Partial — see below** |
| 8 | RAG inspector UI | ✅ **Done — verified** |
| 9 | Agent loop, tools, Docker sandbox, patch proposal + diff viewer | ⚠️ **Built — model-limited** |

### Stage 2 — multi-agent orchestration

Not started beyond its entry condition. [ADR-007](adr/ADR-007-multi-agent-deferred.md)
makes recorded agent metrics the precondition, so milestone 1 exists to satisfy
that before any specialisation is built.

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Agent benchmark + Stage 1 baseline | ✅ **Done — recorded below** |
| 2 | Persisted `AgentState`, agent identity on tool runs and events | ⏸️ **Paused — see baseline** |
| 3 | Agent roles with scoped permissions (Research, Coding, Testing, Review) | ⏸️ **Paused** |
| 4 | Manager and handoffs | ⏸️ **Paused** |
| 5 | Replay UI over stored events | ⬜ Not started |
| 6 | Branch and PR creation behind the approval gate | ⬜ Not started |
| 7 | Measure against the baseline; keep or revert | ⬜ Not started |

**Milestones 2–4 are paused.** The baseline below shows the single agent
failing at reasoning, not at coordination — which is not what specialisation
addresses. They wait behind [ADR-013](adr/ADR-013-cloud-llm-provider.md).

[ADR-014](adr/ADR-014-agent-handoff-mechanism.md) drafts the handoff mechanism
for milestone 4. It is a draft: recommendation stated, decision not taken.

#### Stage 1 agent baseline

`qwen2.5-coder:3b`, 12 tasks, one run each, iteration cap 6, commit `39a65ac`
(1,110 embedded chunks), `repo:read` only.

| Metric | Value |
| --- | --- |
| task success (named expected file or symbol) | **0.333** |
| named the expected symbol | 0.250 |
| tool validity (accepted / all calls) | **1.000** |
| chose only reasonable tools | 0.667 |
| mean iterations | 3.25 |
| median run duration | 110.2 s |
| terminal status | 10 succeeded, 2 `max_iterations_exceeded` |

| Task shape | n | success | symbol | mean iterations |
| --- | --- | --- | --- | --- |
| lookup | 8 | 0.500 | 0.375 | 3.25 |
| investigation | 4 | **0.000** | 0.000 | 3.25 |

**The protocol is not the problem. Tool validity was 1.000** — across 39 tool
calls the model never invented a tool name and never produced arguments that
failed validation. The guardrails built in milestone 9 were never needed in
this run. Whatever is failing, it is not the agent contract.

**Investigation tasks scored 0 of 4.** Both `max_iterations_exceeded` runs
spent five or six iterations on repeated `read_file` calls without converging.
The loop terminates correctly and returns partial state; the model simply does
not synthesise across steps.

**Several answers were confidently wrong**, which is worse than not answering:

- *"Where is the agent's maximum iteration limit enforced?"* → "in the
  `run_baseline` function of `agent_runner.py`" — that is the benchmark
  harness, not the agent loop.
- *"What prevents one user from retrieving another user's code?"* → "Access
  control is not implemented in the backend." Flatly false, and tested against
  in three places.
- *"How is a patch validated before a human sees it?"* → described the approval
  gate rather than sandbox validation.

**A limitation of the metric, not only of the model.** For the sandbox network
question the agent answered "using the `--network none` flag in the Docker run
command" — substantively correct — but scored as a miss because it named
neither the file nor `build_command`. The proxy is strict about *citation*, not
about correctness, and 0.333 therefore understates substantive accuracy
slightly. It also cannot catch the reverse, which is why the confidently-wrong
answers above are quoted rather than summarised.

**A methodological problem to fix before the next run.** The re-index included
`eval/`, so the benchmark's own task text is now in the corpus the agent
searches — which is how it found `agent_runner.py` and mislocated the iteration
cap there. Future runs should exclude `eval/` from the index or measure against
a repository that does not contain the benchmark.

**n = 12, one run per task, no variance estimate.** Small differences are
noise. The gap that matters here is not small: 0 of 4 on investigation tasks.

#### Why milestones 2–4 are paused

ADR-007 permits Stage 2 *"only if measurements show the single agent failing in
ways specialisation actually addresses."* These measurements show the opposite.
Specialisation addresses coordination — division of labour, scoped context,
review. Coordination is the part that already works: perfect tool validity, a
loop that terminates correctly, traces that record every step. What fails is
the model's reasoning, and a Manager, Research, Coding, Testing and Review
agent would all run *the same 3B model*, each contributing its own version of
the errors above, with handoffs added between them.

Resolving [ADR-013](adr/ADR-013-cloud-llm-provider.md) is the change that would
move these numbers. Re-running this benchmark on a stronger model is the first
thing to do afterwards, and it is now a single command.

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

### Milestone 7 — what was verified, and what was not

**Reranking: done, and it is the largest measured gain in the project.**

Milestone 6's finding 4 — that tests and documentation out-rank
implementation — turned out to be the dominant problem. A role-weighted
reranker (source 1.0, config 0.8, docs 0.7, test 0.6) was measured on the
**held-out** question set, written before any tuning and used for exactly one
confirmatory run:

| configuration | R@1 | R@5 | R@10 | MRR |
| --- | --- | --- | --- | --- |
| vector only | 0.429 | 0.607 | 0.714 | 0.664 |
| hybrid | 0.321 | 0.750 | 0.786 | 0.661 |
| **hybrid + role weighting** | **0.571** | **0.821** | **0.821** | **0.774** |

It is the bigger of the two effects. Hybrid over vector-only gained +0.143 R@5
and nothing on MRR; role weighting added a further +0.071 R@5 and **+0.113
MRR** on top, for the cost of a dictionary lookup.

**The specified cross-encoder was not shipped.** `bge-reranker-base` was
implemented and measured, then removed. It segfaulted (`ACCESS_VIOLATION`)
partway through benchmark runs at two batch sizes with threading pinned, and
before crashing logged **22–29 seconds to score 20 query/chunk pairs** on CPU.
A probe also suggested it prefers prose to code — it scored a documentation
paragraph 0.998 against the `encrypt_token` function's 0.095 — which would
worsen the very problem role weighting fixes. Full reasoning and evidence in
[ADR-012](adr/ADR-012-role-weighted-reranking.md).

**Chat: the pipeline works. The answers are not trustworthy on this hardware.**

Retrieval → rerank → bounded context → generation → citation verification runs
end to end against the real index and a real model. Context assembly enforces a
token budget, labels repository content as untrusted data inside explicit
delimiters, and numbers sources so citations are checkable.

**The 7B model does not fit.** This machine has 7.7 GB RAM with ~0.6 GB free;
`qwen2.5-coder:7b` needs 4.7 GB and Ollama fails to allocate it. Two smaller
models of the same family were measured instead.

| | `qwen2.5-coder:1.5b` | `qwen2.5-coder:3b` |
| --- | --- | --- |
| Refuses an off-topic question | ❌ invented "45 mph" | ✅ "I cannot answer this question. It is not related to the codebase provided." |
| Cited any source | 0 of 3 substantive questions | 1 of 3 (coverage 0.333) |
| Factual errors in answers | yes | yes |
| Generation time | 7–20 s | 12–13 s |

**3b fixed fabrication; it did not fix citation.** Asked the airspeed velocity
of an unladen swallow, 1.5b answered "approximately 47 mph. This information is
provided in the source code at [4]" — citing a real retrieved chunk for a claim
found nowhere in it. 3b refuses cleanly. But 3b still cited on only one of
three real questions, and still made inverted claims: it said `is_secret_path`
returns `False` for a secret path (it returns `True`), and attributed
encryption to `decrypt_token` rather than `encrypt_token` — with the correct
chunks in front of it both times.

**Retrieval is not the bottleneck; the model is.** For the `is_secret_path`
question, three of the six sources were `filters.py` chunks including
`classify` and `SkipReason`. The evidence was there and was misread.

**Citation validity is not groundedness.** The checker correctly reported
`valid: true` for an answer that was entirely fabricated, because the cited
index existed. Mechanical checks catch invented source numbers; they cannot
catch invented claims. Groundedness needs a judge, and an unvalidated judge
would be a number with nothing behind it — so it is **not implemented and not
claimed**, rather than approximated.

#### A metric bug found while comparing the two models

The first reading of these results was wrong, and the correction matters more
than the original number. Citation coverage was reported as **0.0 for every
answer**, and that was taken as "the model does not cite". It was a bug in the
metric: sentences were split on `.!?`, so the period inside
`` `backend/app/core/security.py` `` cut the sentence in two and left a
fragment holding only ``py` [3].`` — which the length guard then discarded. A
real citation was scored as none.

In an answer *about code*, periods that are not sentence ends are the common
case, not an edge case. The splitter now masks inline code spans before
splitting and counts a fragment only if it holds two or more word-like tokens,
with regression tests over the exact answers that exposed it. 1.5b genuinely
did not cite — it emitted no `[n]` markers at all — but 3b's 0.333 had been
reported as 0.0.

#### Verdict: not good enough for citation-grade answers

The pipeline is sound and retrieval is measurably good. The models that fit in
7.7 GB are not. A larger model — cloud or a machine with more memory — is the
next step, and the only code change it needs is a provider behind the same
interface: `app/llm/chat.py` currently speaks Ollama's `/api/chat` directly.

- Quality gate: `ruff` clean, `mypy --strict` clean on 70 files, 234 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.

### Milestone 8 — what was verified

The inspector shows **every** candidate the retrievers produced, not just the
ones selected. Verified against a real query on the live index — "what stops
secret files from being indexed", 81 fused candidates from 50 vector + 50
keyword:

- All 81 are returned, in the order the reranker produced, with `selected`
  marking the 6 that reached the answer's context. The selected ids match
  `results` exactly.
- Each row carries both retrievers' own score **and rank**, the reranker score,
  and the file role. Absent scores render as `—` rather than 0.0000, so
  "not scored by this retriever" cannot be misread as "scored badly".
- Filtering by method, role, or selected-only does not renumber rows: rank is
  position in the full ordered list, so a filtered view cannot misrepresent
  where something actually placed.

It immediately made two things visible that were previously only inferable:

- **`README.md` had the highest fused score of all 81 candidates** (0.03028)
  and was demoted to rank 9 by role weighting. ADR-012's mechanism, shown
  rather than argued.
- **`is_secret_path` — arguably the best answer — sat at rank 18**, found by
  vector search alone. Keyword search missed it entirely. That is a concrete,
  actionable retrieval gap the aggregate metrics could not point at.

Search was also switched to the same reranker chat uses. An inspector
explaining a different configuration from the one that produces answers would
be worse than no inspector, and the two had silently diverged when role
weighting was added to chat.

- Quality gate: `ruff` clean, `mypy --strict` clean, 242 tests passing,
  `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.

### Not yet decided: where answers are generated

[ADR-013](adr/ADR-013-cloud-llm-provider.md) is a **draft**, written so the
options are laid out before the decision rather than after it. It covers
staying local, moving to a cloud LLM, or a provider interface with per-
repository opt-in, and it treats the security trade-off — private repository
source leaving the machine — as the substance of the decision rather than a
footnote. No decision has been made.

### Milestone 9 — what was verified

**The sandbox boundary is real, and each property is asserted by an attempt to
break it** (ADR-006 says these are tested rather than assumed). Against real
containers: outbound connections refused, DNS unresolvable, uid 65534 not root,
writes to `/` rejected while the workspace mount works, nothing of the host
visible, a 120s sleep killed at a 5s timeout with the container confirmed gone
afterwards, a 400 MB allocation stopped under a 128 MB limit, oversized output
truncated with a marker. The flag list is also asserted without starting a
container, so silently dropping one fails there.

**Guardrails hold regardless of what the model asks for.** All five
path-traversal spellings refused; permission checked *before* arguments are
parsed, so a refusal never depends on the call being well-formed; filesystem
tools refusing rather than falling back to the host when no workspace exists.
`run_tests` builds its own argv — the agent supplies a path-checked target,
never an interpreter or flags, because a tool that takes a command line is a
shell.

**Patches are gated three ways.** Parsing is not applying (the diff is
inspected on the host, nothing written). Applying happens in the container
against a *copy*, so a half-applied patch cannot corrupt the snapshot the run
is reading. Validation is not approval. A test bypasses the parser deliberately
to prove the container-side path check is real rather than decorative.

**A real run with the local model** (`qwen2.5-coder:3b`, 5-iteration cap):

| Task | Iterations | Outcome |
| --- | --- | --- |
| "Which function verifies the OAuth state parameter?" | 4 | Found the right file area; concluded the invented name it searched for was absent |
| "How are secret files stopped from being indexed?" | 2 | Named `filters.py` correctly; attributed the mechanism to the wrong symbol |

The machinery works. Iteration 2 of the first run is the interesting one: the
model asked to `read_file`, the tool refused because no workspace was mounted,
the refusal was fed back in words it could act on, and it chose `search_symbol`
instead. That is the whole design — the model chooses, the code decides — doing
its job on a live run rather than in a fixture. Traces recorded 12 and 6 events
in correct sequence order, and both runs reached a terminal state with
timestamps persisted.

**The model's decisions are poor, as expected.** It anchored on a function name
it invented (`verify_oauth_state`) rather than searching for the concept, and
it identified the right file while describing the wrong mechanism. This is the
same ceiling chat hit in milestone 7, and it is why milestone 9 is recorded as
*built, model-limited* rather than done. **No patch was generated in a live
run** — producing a valid unified diff is beyond this model, and the patch path
is verified against fixtures rather than model output.

Retrieval is not the bottleneck; the model is. [ADR-013](adr/ADR-013-cloud-llm-provider.md)
is the lever, not more agent code.

#### Two findings from the live run

**The stored GitHub token had expired.** The first end-to-end attempt failed
with `GitHub returned 401`. The OAuth App is a GitHub App (client id `Ov23li…`),
whose user tokens expire after roughly eight hours. Two consequences: the run
was re-done without the snapshot (search tools read the database, so the loop,
guardrails and trace were still exercised with the real model), and the error
message was fixed — a 401 during fetch now says the authorisation expired and
to sign in again, rather than surfacing a bare status code.

**`python:3.11-slim` has no pytest**, and `--network none` means it cannot be
installed, so patch validation could never have run. Fixed the way ADR-006 says
to: a pre-built image (`docker/sandbox.Dockerfile`) built where the contents are
reviewable and the build still has a network. The runner checks the image exists
and names the build command rather than failing with an unrecoverable pull
error.

- Quality gate: `ruff` clean, `mypy --strict` clean on 82 files, 336 tests
  passing, `tsc --noEmit` clean, `eslint` clean, `next build` succeeding.
