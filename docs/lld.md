# Low-Level Design — Stage 1

Modules marked *(planned)* do not exist yet.

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
│   ├── llm/                    EmbeddingProvider + Ollama implementation
│   ├── agent/                  (planned) engine, state, tools/
│   ├── sandbox/                (planned) Docker runner
│   ├── queue/                  queue interface + RQ backend (ADR-003)
│   └── workers/                worker entrypoints
├── migrations/                 Alembic
├── tests/{unit,integration}/
└── eval/                       benchmark, metrics, runner, saved results
```

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

## Planned modules

Interfaces are recorded here as design intent. They are not yet implemented.

```
IngestionService: clone_repository, discover_files, filter_files,
                  parse_file, chunk_file
Chunker:          detect_language, parse_ast, extract_symbols,
                  create_function_chunks, create_class_chunks, attach_metadata
RAGService:       process_query, vector_search, keyword_search, merge_results,
                  rerank, deduplicate, build_context, generate_citations
LLMProvider:      generate, stream, structured_output, tool_call
AgentEngine:      create_run, load_state, plan, select_action, execute_tool,
                  observe, validate, update_state, finalize
Tool:             name, description, input_schema, permissions, execute
SandboxManager:   create_container, configure_limits, mount_workspace,
                  execute_command, capture_logs, destroy_container
JobQueue:         enqueue_job, process_job          # Kafka swap point, ADR-004
```

## Frontend layout

```
frontend/src/
├── app/            App Router pages
├── features/       feature-scoped components (health, repos, chat, ...)
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

`runner.py` measures three configurations over the same questions, using the
`use_vector` / `use_keyword` flags on `retrieve`. Reporting hybrid alone would
be an assertion; the baselines are the evidence. It refuses to run when a
labelled file is missing from the index, because scoring zero on a moved file
is indistinguishable from a regression.

`__main__.py` is the CLI. Reports are written to `eval/results/` and the newest
is served read-only by `GET /evaluations`.
