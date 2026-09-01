# Database design

PostgreSQL 16 with the `pgvector` extension — one database for relational data
and embeddings (ADR-001).

## Conventions

- UUID primary keys, safe to expose in URLs and logs.
- `created_at` / `updated_at` on every entity, database-generated.
- Foreign keys are always declared, with `ON DELETE CASCADE` down the
  repository → file → chunk tree so orphans cannot exist.
- `jsonb` only for genuinely open-shaped payloads (tool I/O, event metadata).
  Anything queried or constrained gets a real column.
- Every schema change is an Alembic migration. `alembic check` runs in CI to
  catch models drifting from migrations.

## Implemented tables

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `github_id` | bigint | unique, indexed. Not `int` — GitHub IDs are not guaranteed to fit in 32 bits |
| `login` | varchar(255) | |
| `email`, `name`, `avatar_url` | nullable | GitHub may not expose an email |

### `repositories`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `user_id` | uuid FK → `users.id` | cascade delete, indexed |
| `github_id` | bigint | |
| `owner`, `name` | varchar(255) | |
| `default_branch` | varchar(255) | |
| `is_private` | boolean | |
| `current_commit` | varchar(40), null | SHA the current index was built from |
| `index_status` | enum | `not_indexed \| queued \| indexing \| indexed \| failed` |
| `indexed_at` | timestamptz, null | |

Unique on `(user_id, github_id)`: a user connects a repository once, while two
users may each connect the same public repository independently.

`index_status` is stored as a checked varchar rather than a native PG enum,
because adding a value to a native enum requires a migration that cannot run
inside a transaction.

### `files`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `repository_id` | uuid FK → `repositories.id` | cascade delete, indexed |
| `path` | text | repository-relative, forward slashes on every platform |
| `language` | varchar(32), null | null means no grammar — chunked by size |
| `content_hash` | varchar(64) | SHA-256 of the bytes; drives incremental re-indexing |
| `commit_sha` | varchar(40) | the revision this content came from |
| `size_bytes` | integer | |

Unique on `(repository_id, path)`: re-indexing updates the row in place, so the
table stays the size of the working tree rather than the size of history.

### `code_chunks`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `file_id` | uuid FK → `files.id` | cascade delete, indexed |
| `repository_id` | uuid FK → `repositories.id` | denormalised; see below |
| `content` | text | |
| `symbol` | varchar(512), null | qualified where useful (`Worker.run`) |
| `kind` | enum | `function \| method \| class \| block \| fragment \| fallback` |
| `start_line`, `end_line` | integer | 1-based, inclusive — what citations point at |
| `chunk_hash` | varchar(64) | SHA-256 of the content |
| `content_tsv` | tsvector GENERATED | keyword retrieval, milestone 5 |

`embedding vector(768)` and `embedding_model` arrive in **milestone 4**, with
the HNSW index, rather than being created empty now.

`chunk_hash` is indexed only through the composite
`(repository_id, chunk_hash)`. Every lookup is already scoped to a repository,
so a second standalone index would never be the one chosen.

### `jobs`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `type` | enum | `index_repository` |
| `status` | enum | `queued \| running \| succeeded \| failed`, indexed |
| `repository_id` | uuid FK → `repositories.id` | cascade delete, indexed |
| `progress` | integer | 0–100, coarse by design |
| `stage` | varchar(64), null | short human-readable stage |
| `started_at`, `finished_at` | timestamptz, null | |
| `error` | text, null | only messages safe to show a user |

Redis holds the queue; this table holds the record. Keeping the record in
Postgres is what makes a lost Redis job detectable rather than silent (ADR-003),
and gives the UI something durable to poll.

## Planned tables

Shape is settled; they are created in the milestone that first needs them.

```
agent_runs   (id, trace_id, repository_id FK, task, status, iterations,
              started_at, finished_at)

tool_runs    (id, agent_run_id FK, tool_name, status, duration_ms,
              input jsonb, output jsonb)

test_runs    (id, agent_run_id FK, status, command, stdout, stderr,
              exit_code, duration_ms)

patches      (id, agent_run_id FK, diff, status, created_at)

events       (id, trace_id, event_type, component, ts, duration_ms,
              status, metadata jsonb)
```

### Two deliberate choices

**`repository_id` is denormalised onto `code_chunks`.** It is derivable through
`files`, but repository isolation must be enforced on every retrieval query. As
a column it is a single indexed predicate on the hot path instead of a join, and
the isolation rule becomes hard to write incorrectly.

**`content_tsv` is a generated column.** The keyword side of hybrid search reads
it directly, and generating it in the database means it cannot drift out of sync
with `content`.

## Indexes

| Index | Purpose |
| --- | --- |
| HNSW on `code_chunks.embedding` (`vector_cosine_ops`) | semantic retrieval — milestone 4 |
| GIN on `code_chunks.content_tsv` | keyword retrieval — built |
| btree on `code_chunks.repository_id` | repository isolation filter — built |
| btree on `(code_chunks.repository_id, chunk_hash)` | skip re-embedding unchanged chunks — built |
| btree on `events.trace_id` | trace reconstruction |

HNSW is chosen over IVFFlat because it does not need a training step and behaves
well as rows are added incrementally, which matches per-repository indexing.

## Migrations

```bash
cd backend
alembic upgrade head                       # apply
alembic revision --autogenerate -m "..."   # create (always review the output)
alembic check                              # fail if models drift from schema
```

Autogenerated migrations are reviewed, never applied blind — the first migration
needed a hand-added `CREATE EXTENSION IF NOT EXISTS vector` and a corrected
`BigInteger` column type.
