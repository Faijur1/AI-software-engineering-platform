# API design

REST over JSON. Requests and responses are validated by Pydantic models;
handlers never return ad-hoc dictionaries.

## Error envelope

Every failure — including validation and unexpected exceptions — returns:

```json
{
  "error": {
    "code": "not_found",
    "message": "Repository not found",
    "trace_id": "0f473d5af2504959af708045358f7711",
    "details": {}
  }
}
```

`code` is stable and machine-readable; the frontend switches on it rather than
on message text. `trace_id` ties the response to server logs. Internal
exception detail is never included: unexpected errors are logged in full
server-side and returned as an opaque `internal_error`.

| Code | Status | Meaning |
| --- | --- | --- |
| `validation_error` | 422 | Request failed schema validation |
| `unauthenticated` | 401 | Missing or invalid session |
| `forbidden` | 403 | Authenticated but not permitted |
| `not_found` | 404 | Resource does not exist, or is not visible to the caller |
| `conflict` | 409 | Conflicts with current state |
| `external_service_error` | 502 | Upstream (GitHub, Ollama, …) failed |
| `internal_error` | 500 | Unexpected — details are in the logs only |

A resource belonging to another user returns 404, not 403: existence itself is
not disclosed.

## Correlation

Every response carries `X-Trace-Id`. Clients may send one to extend an existing
trace across the frontend/backend boundary.

## Implemented endpoints

### `GET /health`

Reports live dependency connectivity. Returns **200** when everything is
reachable, **503** when any dependency is not. Never cached.

```json
{
  "status": "ok",
  "environment": "development",
  "dependencies": {
    "database": { "status": "ok", "latency_ms": 5.5, "error": null },
    "redis":    { "status": "ok", "latency_ms": 1.2, "error": null },
    "ollama":   { "status": "ok", "latency_ms": 8.0, "error": null }
  }
}
```

`ollama` checks that the configured **embedding model is pulled**, not merely
that the server answers — a running Ollama with no model fails every indexing
job, so reporting it healthy would be misleading.

When a dependency is down, `status` becomes `degraded` and that dependency
reports `{"status": "unavailable", "error": "ConnectionError"}`. `error` is the
exception *type* only — connection errors can embed credential-bearing DSNs.

### Authentication

The backend owns the GitHub OAuth round trip (ADR-009). Authenticated endpoints
read an HttpOnly session cookie; without a valid one they return **401
`unauthenticated`**.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/auth/github/login` | 307 to GitHub; pins a CSRF `state` cookie |
| GET | `/auth/github/callback` | Exchanges the code, sets the session, 303 to the frontend |
| GET | `/auth/me` | The signed-in user |
| POST | `/auth/logout` | Clears the session; 204 even without one |

The callback never renders an error page. Failures redirect to the frontend
with `?auth_error=<code>` — `access_denied`, `invalid_state`, `missing_code`,
or the error envelope's `code`.

### Repositories

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/repositories` | Repositories connected to this platform |
| POST | `/repositories` | Connect one by `{owner, name}`; 201, idempotent |
| DELETE | `/repositories/{id}` | Disconnect; 204 |
| GET | `/repositories/github` | Live listing from GitHub, paginated |

`GET /repositories` includes `file_count`, `chunk_count` and
`embedded_chunks`, counted from the database. `embedded_chunks` below
`chunk_count` means a partial embedding pass, and is reported as such rather
than rounded up — it is the difference between a searchable index and one that
silently misses results.

`GET /repositories/github` returns `{items, page, per_page, has_next}`. There is
no total: GitHub does not report one for this endpoint, and inventing one would
be a fabricated number. Each item carries `connected_id` — the local id if it is
already connected, else `null`.

`POST /repositories` takes owner and name, never a GitHub id. The server
re-fetches the repository with the caller's own token; being able to see it
there *is* the authorisation check. A repository that token cannot see returns
**404 `not_found`** — GitHub itself answers 404 rather than 403 for exactly the
same reason, so the status maps straight through.

### Indexing

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/repositories/{id}/index` | Queue indexing; **202** with a job |
| GET | `/jobs/{id}` | Job status and progress |

`PATCH /repositories/{id}/settings` is the only way to grant or withdraw
permission for a repository's code to be sent to a cloud model provider. Owner
scoped, and a non-owner gets 404 rather than 403 so existence is not disclosed.
The grant is never implied: connecting a repository, or enabling a provider in
configuration, leaves every repository denied. Withdrawal takes effect on the
next question and cannot recall what was already sent.

`POST /repositories/{id}/index` returns **202 Accepted**, never 200: the work
has been accepted, not performed. Indexing runs for minutes in a background
worker, so no HTTP request waits for it.

Queueing a repository that is already `queued` or `running` returns the
**existing** job rather than creating a second one — two workers writing the
same chunks would corrupt the index, and a double-click should be harmless.

`GET /jobs/{id}` is joined to the owning repository and filtered on the caller,
so a job id alone is not enough to read another user's progress.

```json
{
  "id": "…",
  "type": "index_repository",
  "status": "running",
  "repository_id": "…",
  "progress": 45,
  "stage": "parsing and chunking",
  "error": null
}
```

`status` is `queued | running | succeeded | failed`. `progress` is coarse and
tied to stage boundaries — it exists to show that work is advancing, not to
estimate completion. `error` carries only messages already judged safe to show
a user; an unexpected exception is logged in full and reported generically.

### Search

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/repositories/{id}/search` | Hybrid retrieval over one repository |

Synchronous, unlike indexing: one embedding call plus two indexed queries, so
it answers in well under a second and needs no job.

Request is `{query, limit, include_candidates}`. The response carries
per-result evidence, not just a ranking:

```json
{
  "results": [{
    "file_path": "backend/app/routes/auth.py",
    "symbol": "github_callback",
    "start_line": 91, "end_line": 142,
    "method": "both",
    "fused_score": 0.0313,
    "vector_score": 0.652, "vector_rank": 7,
    "keyword_score": 0.0030, "keyword_rank": 1,
    "rerank_score": null
  }],
  "vector_candidates": 50, "keyword_candidates": 2, "fused_candidates": 50,
  "notes": [],
  "reranker": "passthrough", "reranker_is_passthrough": true
}
```

`method` is `vector`, `keyword` or `both`. `fused_score` is RRF (ADR-011) and
is comparable **only within one query's results** — it is not a relevance
percentage. Each retriever's own score and rank are returned so a ranking can
be explained; that is what the inspector reads in milestone 8.

`rerank_score` is `null` while the reranker is a passthrough. Null means *not
reranked*, never *reranked and unchanged*, and `reranker_is_passthrough` says
so explicitly so no UI implies a step that has not happened.

With `include_candidates: true` the response also carries `candidates` — every
fused candidate in final rerank order, each with `selected` and `role`. This is
what the RAG inspector reads. It is off by default because the payload is much
larger, and `null` means *not requested*, never *none found*.

`notes` reports degradation: a stopword-only query, or an embedding backend
that is down. A half-working hybrid search is stated rather than silently
returned as if whole.

Searching a repository with no embedded chunks returns **422**, not an empty
list: "nothing indexed" is a missing step the user can fix, and "nothing
matched" is a real answer.

### Evaluation

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/evaluations` | The most recent benchmark run |

Reads reports written by `python -m eval`; it never runs the benchmark. A run
takes minutes and needs the embedding model, so triggering it from an HTTP
request would be the wrong shape. With no run yet, it returns **404** with an
instruction rather than zeros — zeros would read as a catastrophic result
rather than as no data. A truncated report is treated the same way.

Each configuration reports `recall`, `precision` and `hit_rate` keyed by
cutoff, plus `mrr` and a `by_style` breakdown. `reranker` names what was active
during the run; while it reads `passthrough`, the numbers are the pre-reranking
baseline.

### Agents, traces and patches

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/agents/run` | Queue an agent run; **202** with a record to poll |
| GET | `/agents/runs` | Recent runs, newest first (bounded) |
| GET | `/agents/runs/{id}` | A run, its tool calls and its patch ids |
| GET | `/traces/{id}` | Ordered events for one run |
| POST | `/patches` | Propose a patch from a run |
| GET | `/patches/{id}` | A patch and its diff |
| POST | `/patches/{id}/approve` | Approve or reject; a human action |

`POST /agents/run` returns **202**: a run is minutes of model calls and
sandboxed execution, so no request waits for it. `max_iterations` is bounded in
the schema (1–20), not merely defaulted — the cap is what stops a confused
model looping, so a client must not be able to raise it arbitrarily.

`allow_tests` defaults to **false**. Running a repository's test suite is a far
larger capability than reading it, so `sandbox:execute` is granted per run and
never assumed.

A run's `status` is `queued | running | succeeded | failed |
max_iterations_exceeded`. The last is **not** a failure: the run did work and
has partial state worth showing, and collapsing them would hide the difference
between "this went wrong" and "this needed more room".

`tool_runs` includes **rejected** calls — a hallucinated tool name or a refused
path is how a run that went nowhere becomes diagnosable, and tool-selection
accuracy is a metric.

`/traces/{id}` is ordered by sequence, not timestamp: two events can land in
the same millisecond, and an ordering that sometimes inverts is worse than
none.

A patch's `validated` is a **nullable** boolean. Null means *not validated* and
must never be read as passed. Approving an unvalidated patch is allowed —
refusing would be the tool overriding the human — but the record shows exactly
what was known at the time. Deciding twice returns **409**, because
re-approving would overwrite who decided and when.

Runs, traces and patches are all authorised through the owning repository, so
an id alone never discloses another user's work.

## Planned endpoints

| Method | Path | Purpose | Milestone |
| --- | --- | --- | --- |
| POST | `/chat` | Ask a question; answer with citations | 7 |

### Conventions for the ones not yet built

- Long-running work returns `202 Accepted` with a job resource; clients poll
  `GET /jobs/{id}`. No HTTP request blocks on indexing or an agent run.
- Mutating endpoints are scoped to the authenticated user; repository ownership
  is checked in the service layer, not assumed from the URL.
- List endpoints are paginated from the start.
