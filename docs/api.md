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
    "redis":    { "status": "ok", "latency_ms": 1.2, "error": null }
  }
}
```

When a dependency is down, `status` becomes `degraded` and that dependency
reports `{"status": "unavailable", "error": "ConnectionError"}`. `error` is the
exception *type* only — connection errors can embed credential-bearing DSNs.

## Planned endpoints

| Method | Path | Purpose | Milestone |
| --- | --- | --- | --- |
| GET | `/repositories` | Connected repositories | 2 |
| POST | `/repositories/{id}/index` | Queue indexing; returns a job | 3 |
| GET | `/jobs/{id}` | Job status and progress | 3 |
| POST | `/chat` | Ask a question; answer with citations | 7 |
| POST | `/agents/run` | Start an agent run | 9 |
| GET | `/agents/runs/{id}` | Run status and result | 9 |
| GET | `/traces/{id}` | Ordered events for a run | 9 |
| POST | `/patches` | Create a proposed patch | 9 |
| GET | `/patches/{id}` | Patch and its diff | 9 |
| POST | `/patches/{id}/approve` | Approve a patch (human gate) | 9 |
| GET | `/evaluations` | Benchmark results | 6 |

### Conventions for the ones not yet built

- Long-running work returns `202 Accepted` with a job resource; clients poll
  `GET /jobs/{id}`. No HTTP request blocks on indexing or an agent run.
- Mutating endpoints are scoped to the authenticated user; repository ownership
  is checked in the service layer, not assumed from the URL.
- List endpoints are paginated from the start.
