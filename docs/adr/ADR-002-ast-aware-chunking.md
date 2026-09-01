# ADR-002 — AST-aware chunking instead of fixed-size character splitting

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

Retrieved code is shown to the user as evidence and sent to the LLM as context.
The unit of retrieval therefore has to be something a developer recognises.

Splitting source files every N characters cuts functions in half, separates a
signature from its body, and strips the context needed to judge whether a chunk
is relevant. It also produces citations that point at arbitrary line ranges.

## Decision

Parse source files with tree-sitter and chunk by logical unit — function,
method, class, or module-level block — preserving `symbol`, `start_line`, and
`end_line` for every chunk.

## Alternatives considered

- **Fixed-size character/token splitting with overlap.** Trivial to implement,
  language-agnostic, and the usual default. Produces chunks that do not
  correspond to any real code construct.
- **Line-count splitting.** Same problems with a different unit.
- **Whole-file chunks.** Preserves meaning but wastes the context budget and
  retrieves poorly, since one file mixes many unrelated concerns.

## Consequences

**Positive**

- Chunks correspond to real constructs, so citations point at a whole function.
- Symbol names are available as retrievable metadata, which the keyword side of
  hybrid search exploits directly.
- Chunk boundaries are stable across unrelated edits elsewhere in the file,
  which makes incremental re-indexing by `chunk_hash` effective.

**Negative**

- A grammar is needed per language; unsupported languages must fall back to a
  simpler strategy, and that fallback has to be built and tested.
- Very large functions can still exceed the context budget and need splitting.
- Parsing is slower than naive splitting.

**Fallback:** files in languages without a grammar are chunked by size, and
tagged so evaluation can measure retrieval quality separately for them.
