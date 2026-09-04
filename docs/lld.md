# Low-Level Design — Stage 1

Everything described here exists. Where a Stage 1 interface was designed and
then built differently, the section says so rather than describing the design
as though it had been followed.

## Backend layout

```
backend/
├── app/
│   ├── main.py                 application factory
│   ├── core/
│   │   ├── config.py           validated settings, single entry point
│   │   ├── database.py         engine, session factory, session_scope()
│   │   ├── redis_client.py     pooled Redis client
│   │   ├── logging.py          structlog + trace_id context var
│   │   ├── middleware.py       TraceMiddleware (correlation + access log)
│   │   ├── errors.py           exception hierarchy + handlers
│   │   ├── security.py         session signing, token encryption at rest
│   │   └── deps.py             current_user / github_token dependencies
│   ├── models/                 SQLAlchemy ORM
│   ├── schemas/                Pydantic request/response models
│   ├── routes/                 HTTP layer only — no business logic
│   ├── services/               business logic (github.py, users.py)
│   ├── ingestion/              languages, filters, fetcher, discovery, chunker,
│   │                           embedder, service
│   ├── rag/                    vector, keyword, fusion, reranker, retriever
│   ├── llm/                    provider interfaces (types.py), Ollama and
│   │                           Gemini chat providers, embeddings, chat prompt
│   ├── agent/                  engine, tools, tracing, patches
│   ├── sandbox/                Docker runner (ADR-006)
│   ├── queue/                  queue interface, RQ and Kafka backends (ADR-003)
│   ├── events/                  indexing event contract, publishers, recorder,
│   │                           replay (ADR-004)
│   └── workers/                worker entrypoints
├── migrations/                 Alembic
├── scripts/                    CI helpers (annotate_failures.py)
├── tests/{unit,integration}/
└── eval/                       benchmark, metrics, runner, saved results
```

`docker/sandbox.Dockerfile` and its entrypoint sit at the repository root rather
than under `backend/`, because the sandbox image is built from the root context;
`app/sandbox/runner.py` names that exact command when the image is missing.

**Layering rule.** Routes validate and delegate; services hold business logic
and own transaction boundaries; models are persistence only. Routes never
contain query logic, and services never construct HTTP responses.

## Implemented modules

### `core/config.py`

A single `Settings` object built with `pydantic-settings`, cached by
`get_settings()`. Every value is typed and validated at startup, so a malformed
`DATABASE_URL` fails immediately rather than at first query. Modules import
`get_settings()`; nothing reads `os.environ` directly.

### `core/database.py`

Sync engine (ADR-008) with `pool_pre_ping` so a connection dropped by the server
is replaced rather than surfaced as a stale-connection error. `session_scope()`
commits on success, rolls back on any exception, always closes, and re-raises —
failures are never swallowed. `get_db()` adapts it as a FastAPI dependency.

### `core/logging.py`

structlog with a `trace_id` context variable. The ID is bound once at the edge
and merged into every downstream log line, so correlation does not require
threading an argument through every function signature.

### `core/middleware.py`

`TraceMiddleware` honours an inbound `X-Trace-Id` (so a trace can span frontend
and backend) or generates one, logs each request's method, path, status and
duration, and echoes the ID back in the response header.

### `core/errors.py`

`AppError` subclasses carry an HTTP status and a stable machine-readable code.
Every error response uses one envelope. The catch-all handler logs the full
exception server-side and returns an opaque message, so internal detail and
credentials cannot leak to clients — this is asserted by tests.

### `routes/health.py`

Probes Postgres (`SELECT 1`) and Redis (`PING`), reporting per-dependency status
and measured latency, and returning 503 when any dependency is down. Failure
messages are reduced to the exception *type*, because connection errors can
embed a DSN containing credentials.

## Stage 1 interfaces, as designed and as built

These interfaces were recorded at the start of Stage 1 as design intent. Almost
all of them now exist, under names that drifted as the code met the problem. The
mapping is kept rather than deleted: where a name changed, the change is usually
the interesting part.

| Designed | Built as | Note |
| --- | --- | --- |
| `IngestionService` | `ingestion/service.py` — `index_repository` | One entry point, not a class. `clone_repository` became `fetcher.py` fetching a tarball, never a clone (ADR-010) |
| `Chunker` | `ingestion/chunker.py` — `chunk_source`, `Chunk` | `parse_ast` / `extract_symbols` are internal to tree-sitter chunking; the public surface is one function |
| `RAGService` | `rag/` — `retrieve`, plus `vector`, `keyword`, `fusion`, `reranker` | `merge_results` became RRF (ADR-011). `generate_citations` lives in `llm/chat.py`, since citations are a property of the answer, not of retrieval |
| `LLMProvider` | `llm/types.py` (`ChatProvider`), `llm/ollama_chat.py`, `llm/gemini.py`, `llm/base.py` (`EmbeddingProvider`) | Two protocols, not one: chat is selectable by config (ADR-013), embeddings are not, because stored vectors are only comparable within one model |
| `AgentEngine` | `agent/engine.py` — `run_agent`, `parse_action` | A bounded loop rather than the nine-method lifecycle designed; the cap is the property worth having |
| `Tool` | `agent/tools.py` — `Tool`, `Permission`, `ToolContext` | Permissions are enforced in code at dispatch, not documented as a convention |
| `SandboxManager` | `sandbox/runner.py` — `run`, `build_command`, `remove_workspace` | Functions over a class. `build_command` is separate so the security policy can be asserted verbatim in a test |
| `JobQueue` | `queue/base.py` — `enqueue_index_repository`, `enqueue_agent_run` | A `Protocol`, kept narrow as the Kafka swap point (ADR-004) |

**Not built.** `LLMProvider.stream`, `structured_output` and `tool_call` do not
exist. Streaming was never needed by a page that renders a completed answer, and
the other two describe provider features the local model does not offer — the
agent parses actions out of text instead, which is why `parse_action` is written
to tolerate a weak model's output.

## `agent/`

`engine.py` runs a bounded loop. The cap is `AgentRun.max_iterations`, a column
on the run itself, and nothing in the codebase assigns to it after the run is
created — so a model that never converges terminates with
`max_iterations_exceeded` rather than running until something else stops it.
That status is terminal but *not* a failure, and the benchmark counts it
separately — merging the two would hide the difference between wrong and
unfinished.

`parse_action` is deliberately forgiving. The local model emits malformed JSON,
prose around its JSON, and occasionally a tool name it invented; each is a
rejection recorded against the run rather than an exception, because a benchmark
that crashes on bad output measures nothing.

`tools.py` enforces permissions in code at dispatch. A tool declares what it
needs, the run declares what it was granted, and the check is a comparison
neither side can talk its way past. Path arguments go through
`resolve_in_workspace`, which resolves symlinks before comparing, so `..` and a
symlink out of the tree are the same rejection.

`patches.py` applies a proposed diff to a *copy* and runs the tests there, so a
half-applied patch cannot corrupt the snapshot the rest of the run is reading.
It owns the temporary directory's lifetime rather than using
`TemporaryDirectory`, whose cleanup chmods what it cannot delete — see
`sandbox/`.

## `sandbox/`

The hard boundary from ADR-006. `build_command` is a separate function because
its argument list *is* the security policy: no network, read-only root, uid
65534, all capabilities dropped, pids and memory capped. Keeping it separate
lets a test assert the flags verbatim, which a boundary nobody checks would not
have.

`run` kills the container explicitly on timeout. `subprocess` timeouts kill only
the docker *client*, so without that the timeout would be a lie. Teardown then
waits for the daemon to stop listing the container: `--rm` makes removal
asynchronous, so returning as soon as the commands finish left a container alive
often enough to matter on Linux.

`remove_workspace` exists because the sandbox creates files as uid 65534 that
the host user owns neither the files nor the directories of. It falls back to
deleting the tree from inside a root container when the host cannot. Untrusted
code that can make its own workspace undeletable can fill the disk of whatever
runs it, which is a denial of service rather than untidiness.

## `queue/`

`base.py` is a `Protocol` with one method per job kind, kept deliberately narrow
so the RQ backend can be replaced without touching callers (ADR-004). The RQ
backend uses `SimpleWorker` on Windows, which has no `os.fork`.

`factory.py` chooses between them on `QUEUE_BACKEND`. RQ is the default:
ADR-004 scopes Kafka to *events*, and for work dispatch RQ gives per-job retry
and failure visibility a log does not. `kafka_backend.py` exists to show the
interface was a real seam, and it delegates `enqueue_agent_run` back to RQ
because ADR-004 excludes agent runs as a state machine rather than a stream.

## `events/`

Added in Stage 3. `types.py` holds the closed vocabulary of seven indexing
facts and the `DomainEvent` that carries them; an event type outside the set
raises at construction, because an unknown type would be published, persisted
and replayed forever before anyone noticed a consumer skipping it.

`publisher.py` is the seam: `InProcessEventBus` fans out synchronously with no
durability, and `kafka.py` publishes to a retained log instead. `factory.py`
picks one on `EVENT_BACKEND`, defaulting to in-process so a checkout with no
broker still indexes and still records traces.

`recorder.py` is the second consumer ADR-004 required before Kafka could be
adopted. It is stateless and reads the trace id from the event, because a
consumer group sees every run rather than one; and it inserts with
`ON CONFLICT DO NOTHING` against a uniqueness constraint on
`(trace_id, sequence)`, which is what makes at-least-once delivery survivable.

`replay.py` reads the whole log with a fresh consumer group, leaving the
worker's offsets untouched, and reports events delivered and rows written
separately -- a healthy replay delivers everything and writes nothing.

## Frontend layout

```
frontend/src/
├── app/            App Router pages
├── features/       agent, auth, chat, health, repositories, search
└── lib/            api client, config
```

`lib/api.ts` bounds every request with an `AbortController` timeout, so a hung
backend surfaces as an error state with a retry rather than a spinner that never
resolves. Data fetching happens in Server Components; client components are used
only where interactivity requires them.

### `core/security.py`

Two secrets with distinct jobs, deliberately not shared: `SESSION_SECRET` signs
the session cookie (HS256), `TOKEN_ENCRYPTION_KEY` encrypts GitHub access tokens
before storage (Fernet). Both are required and must be at least 32 characters —
a missing or short secret raises `ConfigurationError` rather than falling back
to a default, because a default here would be a permanent, silent hole.

Verification pins both the algorithm and the issuer. Pinning the algorithm
blocks `alg: none` signature stripping; pinning the issuer stops a token minted
by some other service that happens to share the secret from being replayed as a
session.

### `core/deps.py`

`current_user` is the one place a request becomes an identity, so no endpoint
can be written without an authentication check by omission. It loads the user
from the database on every request rather than trusting the token body, so a
deleted account stops working immediately instead of at token expiry.

`github_token` is a separate dependency: endpoints that only need an identity
never decrypt a credential they have no use for.

### `services/github.py`

Every call to github.com goes through here, so the timeout, the failure mapping
and the "never log a token" rule live in one place. Upstream failures become
`ExternalServiceError` (502), which keeps "GitHub is broken" distinguishable
from "we are broken" in both the API contract and the logs.

Two GitHub behaviours are handled explicitly because they are easy to get
wrong: a failed token exchange is reported with **HTTP 200** and an `error` field
in the body, and a user with a private email address gets `null` from `/user`,
requiring a fallback to the verified primary address from `/user/emails`.

### `routes/auth.py`, `routes/repositories.py`

The OAuth `state` is pinned in a short-lived HttpOnly cookie and compared in
constant time, failing closed when either side is absent. The callback never
renders an error page: the user is mid-navigation in a browser, so failures
redirect to the frontend with a stable `auth_error` code and the frontend owns
the presentation.

Repository queries are filtered on `user_id` as well as the primary key, so
another user's repository is indistinguishable from one that does not exist.
Connecting is authorised by re-fetching the repository with the caller's own
token — never by trusting an identifier from the request body.

### `ingestion/`

Five modules, split so each is testable on its own rather than only through a
full run:

`languages.py`
    Extension → grammar name. A language is listed only if its grammar actually
    loads, checked at import; claiming support the parser cannot deliver would
    surface as a failure mid-index rather than as a clean fallback. Parsers are
    cached, because indexing asks for the same handful thousands of times.

`filters.py`
    Policy only, no filesystem. The secret exclusions are ordered ahead of
    every convenience check so a later reordering cannot let a credential
    through, and `is_secret_path` is public specifically so the security tests
    can assert it directly rather than through the pipeline.

`fetcher.py`
    Downloads and safely extracts the tarball (ADR-010).

`discovery.py`
    Filesystem traversal only. Prunes excluded directories instead of walking
    them, never follows symlinks, and treats a strict UTF-8 decode as the real
    binary test — the extension list is only a cheap first pass.

`chunker.py`
    AST chunking with named degradations (ADR-002). Never raises: a file that
    cannot be parsed falls back to size-based chunking.

`service.py`
    Orchestration and persistence. Progress is delivered through a callback
    rather than by touching the job row, so this module knows nothing about how
    progress is reported and can be tested without a queue.

### `workers/`

`ingestion.py` owns the job lifecycle; `service.py` owns the work. Every exit
path leaves the job in a terminal state — a job stuck at `running` forever is
worse than one marked failed, because the user can retry a failure but cannot
interpret a spinner that never stops. Progress updates run in their own short
transactions so they are visible to the polling UI while the long indexing
transaction is still open.

`run_worker.py` selects `SimpleWorker` where `os.fork` is unavailable, which is
what makes the worker run on Windows.

### `llm/`

`base.py` declares `EmbeddingProvider` as a Protocol carrying `model_name` and
`dimensions` alongside `embed`. Both are part of the interface because both are
recorded with every stored vector: an embedding is comparable only to others
from the same model at the same width.

`ollama.py` implements it. Its contract is strict on purpose — it must return
exactly one vector per input at exactly the configured width, or raise. A short
or ragged result would not raise on its own; it would misalign vectors with
chunks and surface much later as unexplained bad retrieval.

`check_available()` is called before a long indexing run so a model that was
never pulled fails in seconds with the exact command to fix it, rather than
after minutes of parsing.

### `ingestion/embedder.py`

Reads its work queue as a query (`embedding IS NULL OR embedding_model != …`)
rather than holding a list, so a first index, a resumed run and a model change
all fall out of one predicate. Batches are flushed as they complete, and the
batch loop re-queries rather than paginating with an offset — each written
batch leaves the pending set, so a fixed offset would skip rows.

### `rag/`

`vector.py` and `keyword.py` are two independent retrievers with the same
output type. Neither knows about the other, which is what keeps fusion a pure
function over two ranked lists and therefore exactly testable.

Both filter on `repository_id` as their first predicate. That is the tenant
isolation boundary, and the reason the column is denormalised onto chunks: it
must be impossible to forget in a join.

`fusion.py` implements RRF (ADR-011). It merges by rank, so the two score
distributions never have to be reconciled, and it keeps both retrievers'
evidence on each result rather than collapsing to one number.

`reranker.py` defines the `Reranker` protocol and ships `PassthroughReranker`.
The protocol carries `is_passthrough` deliberately: a UI that cannot tell a
real reranker from a placeholder invites exactly the unearned confidence this
project is trying to avoid.

`retriever.py` orchestrates. It retrieves wide (~50) and returns narrow (~10),
which is what makes the cross-encoder in milestone 7 a drop-in rather than a
restructuring. Either retriever failing degrades to the other and records a
note, so a stopped Ollama produces keyword-only results with an explanation
rather than an error page.

### `eval/`

`benchmark.py` holds the labelled questions. Each carries a `style` and a
written `rationale` — the rationale exists so a disputed label can be argued
about on the merits rather than quietly changed until a score improves.

`metrics.py` is plain arithmetic with no library behind it, because the value
of this milestone is that the numbers can be checked by hand. `precision_at_k`
divides by `k` rather than by the number of results returned: three results
where ten were allowed is genuinely worse, and normalising by the short list
would hide an under-filled context window instead of penalising it.

`runner.py` measures four configurations over the same questions — vector only,
keyword only, hybrid, and hybrid with role-weighted reranking — using the
`use_vector` / `use_keyword` flags on `retrieve`. The reranker is part of a
configuration rather than a separate axis, so "hybrid" always means the same
thing and the reranked row is directly comparable to it. Reporting hybrid alone would
be an assertion; the baselines are the evidence. It refuses to run when a
labelled file is missing from the index, because scoring zero on a moved file
is indistinguishable from a regression.

`__main__.py` is the CLI. Reports are written to `eval/results/` and the newest
is served read-only by `GET /evaluations`.
