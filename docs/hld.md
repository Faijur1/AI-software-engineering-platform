# High-Level Design

## Purpose

Connect a GitHub repository, make it searchable and understandable through
retrieval-augmented generation, and let an agent use tools to investigate
problems, run tests safely, and propose patches for human approval.

## Stage 1 architecture

```
                         Browser
                            |
                  Next.js / React (TypeScript)
                            |  HTTP + JSON
                            v
                     FastAPI backend
                            |
        +-------------------+-------------------+
        |                   |                   |
  GitHub Service       RAG Service        Agent Service
        |                   |                   |
        |              pgvector +          Tools + Sandbox
        |              full-text                |
        +---------+---------+---------+---------+
                            |
                   Redis + RQ (job queue)
                            |
             Ingestion / Embedding workers
                            |
                  PostgreSQL + pgvector
                            |
                   LLM layer (Ollama)
```

## Components

| Component | Responsibility | Status |
| --- | --- | --- |
| Frontend | Repository browser, chat, code and diff viewers, RAG inspector, agent trace | Skeleton only |
| API | HTTP surface, validation, auth, error envelope | `/health` only |
| GitHub service | Repository listing, metadata, file fetch. Read-only in Stage 1 | Planned |
| Ingestion | Clone, discover, filter, parse, chunk | Planned |
| RAG | Query processing, hybrid retrieval, merge, rerank, context, citations | Planned |
| LLM layer | Provider-agnostic generation and embeddings | Planned |
| Agent engine | Single loop with a hard iteration cap | Planned |
| Tool system | Small permissioned tool set | Planned |
| Sandbox | Isolated Docker execution (ADR-006) | Planned |
| Job queue | Redis + RQ behind an interface (ADR-003) | Planned |
| Database | PostgreSQL + pgvector (ADR-001) | Built |
| Evaluation | Retrieval and agent benchmarks | Planned |

## Data flows

**Ingestion**

```
GitHub -> repository service -> job queue -> ingestion worker
      -> discover files -> filter -> tree-sitter parse -> chunk
      -> embedding worker -> embedding model -> pgvector
```

**Question (RAG)**

```
query -> preprocess -> [vector search + keyword search]
      -> normalise -> merge -> dedupe -> rerank
      -> build context (token budget) -> LLM -> answer + citations
```

**Agent task (Stage 1)**

```
task -> plan -> select tool -> execute -> observe -> update state
     -> (loop, bounded by max iterations) -> validate
     -> final answer or proposed patch
```

## Trust boundaries

Three boundaries are treated as security-critical and are the organising
principle of the design:

1. **Repository content is untrusted data, never instructions.** A README or
   comment that says "ignore your instructions" is data being summarised, not a
   command. Tool permissions are enforced in code, not by prompt wording.
2. **The sandbox is an isolation boundary.** Repository and AI-generated code
   execute only inside a network-less, resource-limited, disposable container
   (ADR-006).
3. **GitHub writes are approval-gated.** Stage 1 is read-only against GitHub.
   Branch and PR creation arrive in Stage 2, behind explicit human approval.

## Cross-cutting concerns

- **Configuration** enters through one validated settings object; no module
  reads the environment directly.
- **Observability**: every request and job carries a `trace_id`, bound at the
  edge and merged into every structured log line.
- **Error handling**: external dependencies get timeouts and bounded retries.
  Failures are logged and surfaced — never silently swallowed. Internal detail
  is never returned to clients.

## Explicitly out of scope for Stage 1

Kafka, Kubernetes, AWS, multi-agent orchestration, GitHub write operations, and
the full trace-replay UI. Each has an ADR recording when and why it arrives.
