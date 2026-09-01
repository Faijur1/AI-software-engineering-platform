# RAG architecture

> **Status:** ingestion and embedding are **built and verified** as of
> milestone 4. Retrieval, reranking, the inspector and evaluation
> (milestones 5–8) are still design only, and are marked below.

## Ingestion — implemented (milestones 3–4)

```
GitHub -> tarball snapshot -> discover files -> filter -> parse (tree-sitter)
       -> chunk by logical unit -> attach metadata -> embed -> pgvector
```

The whole pipeline runs today.

The snapshot is fetched as a tarball rather than cloned, so the access token
never enters a process argument list (ADR-010). Extraction rejects absolute
paths, `..` traversal and symlinks escaping the tree, and bounds both the
download and the expanded size — repository archives are untrusted input.

Work runs in a background worker via Redis + RQ (ADR-003). `POST
/repositories/{id}/index` returns **202** with a job to poll; no HTTP request
ever waits for indexing.

### Filtering

Excluded from indexing: `.git/`, `node_modules/`, `dist/`, `build/`,
`coverage/`, lockfiles, minified and generated files, binaries, files above a
size threshold (512 KB), and secret-bearing files (`.env`, private keys,
credentials).

Two distinct reasons, worth keeping separate: most exclusions are about noise
and cost, but the secret exclusions are a **security control** — those files
must never reach the LLM.

That separation is enforced in code, not just in prose. The secret check runs
*before* every convenience check, so no later reordering of the cheap rules can
let a credential through, and it is asserted by its own tests. Placeholder
files (`.env.example` and friends) are allowed back in explicitly.

Excluded directories are pruned rather than walked — descending into
`node_modules` to reject each file individually costs hundreds of thousands of
pointless stat calls. A consequence worth stating: files inside a pruned tree
are never counted, so the report gives a count of *pruned directories* rather
than implying a file count nobody measured. Symlinks are never followed; a
repository can link to `/etc` or to itself.

Every skip is counted by reason. A spike in `secret` or `not_utf8` is how a
filtering bug becomes visible instead of silently shrinking the index.

### Chunking

Chunks are logical units — function, method, class, module-level block — not
fixed character windows (ADR-002). Metadata preserved per chunk:

`repository_id`, `file_id`, `path`, `language`, `symbol`, `start_line`,
`end_line`, `commit_sha`, `chunk_hash`, `embedding_model`.

`start_line`/`end_line` are what make citations point at real code, and
`chunk_hash` is what makes re-indexing incremental: a chunk whose hash is
unchanged is not re-embedded. Files in languages without a grammar fall back to
size-based chunking and are tagged so evaluation can measure them separately.

Chunk kinds, and what each signals:

| Kind | Meaning |
| --- | --- |
| `function` | a free function, whole |
| `method` | a function inside a class; the symbol is qualified (`Worker.run`) |
| `class` | a class small enough to be useful whole |
| `block` | a run of module-level statements: imports, constants |
| `fragment` | part of a unit too large for the budget, split by size |
| `fallback` | a file with no grammar, split by size with overlap |

The last two are named degradations, not hidden ones — evaluation can measure
them separately instead of averaging them into the good case.

A class above ~2.5 KB is indexed method by method instead of whole, so a
retrieved chunk is something a developer can read rather than a wall of text
that crowds out everything else in the context window. Decorators and `export`
keywords stay attached to what they apply to: a route's decorator carries its
path, and separating them loses the meaning.

Malformed source never fails a run. tree-sitter is error-tolerant, and a file
that still cannot be parsed falls back to size-based chunking — one broken file
must not fail the index for an entire repository.

### Embedding

A **separate phase** from parsing, run in its own transaction after chunks are
committed. That ordering is deliberate: embedding is the slow, network-bound
part, and if Ollama is unavailable the parsed chunks should already be safe in
the database rather than discarded.

The work queue is a query, not a list: chunks whose `embedding` is NULL, or
whose `embedding_model` is not the model now configured. That single predicate
covers three cases with no extra bookkeeping — a first index, a previous run
that failed partway, and a change of embedding model.

`embedding_model` is stored **per row**, not assumed globally. A vector is only
comparable to others produced by the same model at the same width, so changing
the model must invalidate the old vectors rather than silently mixing two
incompatible spaces — a failure that would show up as quietly degraded
retrieval rather than as an error.

Requests are batched (32 chunks by default). Measured against
`nomic-embed-text`: ~109 ms per chunk batched, against ~700 ms one at a time.

Two failure modes are checked on every response, because neither would raise on
its own: a vector count that does not match the input count (which would pair
vectors with the wrong chunks from that point on), and a vector width that does
not match `EMBEDDING_DIMENSIONS` (which the database column fixes).

### Incremental re-indexing

A file whose `content_hash` is unchanged is not re-parsed, its chunk rows are
reused untouched, and — because the rows survive — their **vectors survive too**
and are not re-computed. A changed file has its old chunks deleted before the
new ones are written, and a file deleted from the repository is removed from
the index, otherwise a deleted function lingers and gets cited as if it still
existed.

Measured on a real repository of 457 chunks: a full index with embedding took
**149 s**; re-indexing with nothing changed took **8 s**.

## Retrieval

```
query -> preprocess -> [ vector search | keyword search ]
      -> normalise scores -> merge -> deduplicate
      -> rerank -> context builder -> LLM
```

### Why hybrid

The two retrievers fail in opposite directions, which is the entire reason for
running both:

- **Vector search** handles conceptual queries ("how is payment retried?") but
  is unreliable for exact identifiers, where an embedding of `parse_config` sits
  near many similar-looking names.
- **Keyword search** (PostgreSQL `tsvector`) is exact for function and class
  names, error strings, file paths, routes and constants, but fails when the
  user's words differ from the code's.

Scores from the two are not comparable, so each result set is normalised before
merging, and a chunk found by both is ranked more highly than one found by
either alone. Deduplication is by chunk ID, retaining the best evidence of
retrieval method for the inspector.

### Reranking

Retrieve a wide candidate set (~50), then narrow to the 5–10 chunks actually
sent to the LLM using a cross-encoder (`bge-reranker-base`). The cross-encoder
sees the query and chunk together, so it can judge relevance in a way that
independent embeddings cannot.

The reranker sits behind an interface. Milestones 5–6 ship a passthrough
implementation — honestly labelled as such — so that the evaluation baseline is
established *before* the real reranker is added and its contribution can be
measured rather than assumed.

### Context building

Enforces a token budget, preserves file and line metadata for citations, and
filters strictly by `repository_id` so chunks from different repositories can
never be mixed.

## RAG inspector

The retrieval path is inspectable in the UI. For every candidate:

file, line range, retrieval method (vector / keyword / both), retrieval score,
reranker score, selected or not, and the code excerpt.

This exists so retrieval failures are diagnosable — the point is to be able to
see *why* an answer was wrong, not merely that it was.

## Evaluation

A labelled benchmark of 20–30 real questions mapped to expected
files/functions/lines, built in milestone 6 — before reranking.

Retrieval metrics: **Recall@K**, **Precision@K**. Answer metrics: groundedness
and citation accuracy. The suite is re-run whenever chunking, the embedding
model, the reranker, or prompts change, so regressions surface immediately.

Numbers are only ever recorded from actual runs.
