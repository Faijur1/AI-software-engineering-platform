# ADR-001 — PostgreSQL + pgvector instead of a dedicated vector database

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

The platform stores relational application data (users, repositories, files,
agent runs) and vector embeddings of code chunks. Retrieval must filter vectors
by repository, and repository isolation is a security requirement: a query must
never return chunks belonging to another user's repository.

## Decision

Store both relational data and embeddings in a single PostgreSQL database, using
the `pgvector` extension for similarity search.

## Alternatives considered

- **Dedicated vector database (Pinecone, Weaviate, Qdrant).** Better raw vector
  performance at very large scale, and richer index tuning.
- **Postgres for metadata + a separate vector store.** The common "hybrid"
  arrangement.

## Consequences

**Positive**

- One datastore to run, back up, migrate, and secure.
- Vectors and their owning rows share transactions and foreign keys, so a chunk
  cannot outlive its file, and a delete cascades correctly.
- Repository isolation is an indexed `WHERE repository_id = ...` predicate
  evaluated in the same engine as the vector search — not a filter applied
  after the fact in application code.
- Hybrid search is simpler: the semantic and keyword sides live in one database
  and can be combined in one query path.

**Negative**

- pgvector will be slower than a specialised engine at very large scale.
- Index tuning options (HNSW parameters) are narrower.

**Revisit if** a single repository's chunk count or query latency makes pgvector
the measured bottleneck. That decision must be driven by benchmark numbers from
the evaluation harness, not by anticipation.
