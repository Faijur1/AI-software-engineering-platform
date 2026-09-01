# RAG architecture

> **Status: planned (milestones 3–8).** None of this is implemented yet. This
> document records the intended design so it can be reviewed before it is built.

## Ingestion

```
GitHub -> clone/fetch -> discover files -> filter -> parse (tree-sitter)
       -> chunk by logical unit -> attach metadata -> embed -> pgvector
```

### Filtering

Excluded from indexing: `.git/`, `node_modules/`, `dist/`, `build/`,
`coverage/`, lockfiles, minified and generated files, binaries, files above a
size threshold, and secret-bearing files (`.env`, private keys, credentials).

Two distinct reasons, worth keeping separate: most exclusions are about noise
and cost, but the secret exclusions are a **security control** — those files
must never reach the LLM.

### Chunking

Chunks are logical units — function, method, class, module-level block — not
fixed character windows (ADR-002). Metadata preserved per chunk:

`repository_id`, `file_id`, `path`, `language`, `symbol`, `start_line`,
`end_line`, `commit_sha`, `chunk_hash`, `embedding_model`.

`start_line`/`end_line` are what make citations point at real code, and
`chunk_hash` is what makes re-indexing incremental: a chunk whose hash is
unchanged is not re-embedded. Files in languages without a grammar fall back to
size-based chunking and are tagged so evaluation can measure them separately.

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
