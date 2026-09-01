# RAG architecture

> **Status:** ingestion, embedding, hybrid retrieval and the **evaluation
> harness** are built and verified as of milestone 6. Reranking is a labelled
> passthrough until milestone 7; the inspector UI (8) is still design only.

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

## Retrieval — implemented (milestone 5)

```
query -> embed --------> vector search  --      -> tsquery ------> keyword search --> RRF fuse -> dedupe -> rerank -> top K
```

Everything up to and including fusion runs today. The reranker is a
**passthrough that says so** (see below), and the context builder arrives with
chat in milestone 7.

`POST /repositories/{id}/search` exposes this, returning each retriever's own
score and rank per result alongside the fused score.

### Query preprocessing

`websearch_to_tsquery` builds the keyword query: it accepts what people
actually type — quoted phrases, `or`, a leading minus — without raising on
punctuation. A query of nothing but stopwords yields an empty tsquery, which is
detected and reported rather than silently matching everything at rank zero.

Two properties of the `english` configuration were **measured against the real
index**, not assumed:

- **`snake_case` is split on underscores.** `github_callback` indexes as
  `'github' 'callback'`, and the same input becomes the phrase
  `'github' <-> 'callback'`. This is what lets an identifier query match a
  definition precisely, and it is what recovers the case vector search misses.
- **`camelCase` is not split.** `OllamaEmbedder` indexes as one stemmed token,
  so searching `Ollama` alone will not find it. A real limitation of this
  configuration; a custom tokeniser would be the fix, and milestone 6 is where
  its cost gets measured rather than guessed at.

`websearch_to_tsquery` also **ANDs** every term, which is far too strict for a
prose question over code — "where is the oauth callback handled in the backend"
requires a chunk containing all six terms. Keyword search therefore tries the
strict query first and retries with the terms OR'd only if it matched nothing:
precision where it is available, recall where it is not. Phrase groupings
(`<->`) survive the relaxation, so split identifiers still match as units.

### Why hybrid

The two retrievers fail in opposite directions, which is the entire reason for
running both:

- **Vector search** handles conceptual queries ("how is payment retried?").
  It was expected to be unreliable for exact identifiers, where an embedding of
  `parse_config` sits near many similar-looking names — but **measurement
  contradicted that** for this corpus and model: vector-only scored 0.833
  recall@5 on identifier questions against keyword-only's 0.667. The assumption
  is left recorded here because it drove the design; the measurement is what
  should be believed.
- **Keyword search** (PostgreSQL `tsvector`) is exact for function and class
  names, error strings, file paths, routes and constants, but fails when the
  user's words differ from the code's.

Scores from the two are not comparable, so results are merged by **rank**
rather than by rescaled score — Reciprocal Rank Fusion, `Σ 1/(k + rank)` with
k=60 (**ADR-011**). A chunk found by both retrievers receives two contributions
and therefore outranks one found by either alone, with no hand-tuned weight.

Measured, the distributions really are unrelated: cosine similarity clusters in
a narrow band (top and tenth results differ by ~0.05), while `ts_rank` ranged
from 0.003 to 0.86 across queries on the same index. Min-max scaling either one
manufactures large apparent differences out of noise, and scales a lone keyword
match to a perfect 1.0.

Deduplication is by chunk id, keeping **both** retrievers' scores and ranks, so
the inspector can explain a ranking rather than assert it. Ties break on chunk
id, so evaluation runs in milestone 6 are reproducible.

A consequence worth stating plainly: **fused scores are not interpretable in
absolute terms.** 0.032 means "near the top of both lists", not "3.2% relevant",
and is comparable only within one query's results.

### Reranking

Retrieve a wide candidate set (~50), then narrow to the 5–10 chunks actually
sent to the LLM using a cross-encoder (`bge-reranker-base`). The cross-encoder
sees the query and chunk together, so it can judge relevance in a way that
independent embeddings cannot.

The reranker sits behind an interface. Milestones 5–6 ship a passthrough
implementation — honestly labelled as such — so that the evaluation baseline is
established *before* the real reranker is added and its contribution can be
measured rather than assumed.

The passthrough truncates and does not reorder. It leaves `rerank_score` as
**null** rather than copying the fused score across, so nothing downstream can
mistake "not reranked" for "reranked and unchanged", and the API reports
`reranker_is_passthrough` so a UI cannot imply a quality step that has not
happened.

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

## Evaluation — implemented (milestone 6)

    python -m eval

26 labelled questions about this codebase, each mapped to the file(s) that
answer it. Labels are paths, not line numbers, so ordinary edits do not
invalidate them, and the harness **refuses to run** if any labelled file is
missing from the index — a stale benchmark silently scoring zero looks exactly
like a retrieval regression.

Relevance is judged at **file** granularity. Whether a retriever returned the
`classify` chunk or the `is_secret_path` chunk of `filters.py` is a distinction
the benchmark has no principled basis to make. Results are deduplicated to one
entry per file before scoring, so a retriever returning several chunks from one
file does not inflate its own precision.

Three configurations are measured over the same questions — vector only,
keyword only, hybrid — because reporting hybrid alone would be an assertion
rather than evidence. The reranker is the passthrough throughout: these are the
pre-reranking baseline that milestone 7 must beat.

The question mix is deliberate and asserted by a test: roughly a third phrased
around an exact identifier, a third conceptual with no shared vocabulary with
the code, a third in between. A benchmark loaded with identifier questions
would flatter keyword search.

Answer metrics — groundedness and citation accuracy — arrive with chat in
milestone 7, since there are no answers to score yet.

Numbers are only ever recorded from actual runs. Results are written to
`eval/results/` and the most recent is served by `GET /evaluations`.

### Baseline results

See [`docs/README.md`](README.md#milestone-6--what-was-verified) for the
measured numbers, the four findings that came out of them, and the two
limitations of the benchmark itself.
