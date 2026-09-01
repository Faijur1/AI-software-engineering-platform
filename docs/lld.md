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
│   ├── ingestion/              (planned) discovery, filters, parser, chunker
│   ├── rag/                    (planned) retrievers, merge, rerank, context
│   ├── llm/                    (planned) LLMProvider + implementations
│   ├── agent/                  (planned) engine, state, tools/
│   ├── sandbox/                (planned) Docker runner
│   ├── queue/                  (planned) queue interface + RQ backend
│   └── workers/                (planned) worker entrypoints
├── migrations/                 Alembic
├── tests/{unit,integration}/
└── eval/                       (planned) benchmark + metrics
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
