"""The labelled retrieval benchmark.

Twenty-six questions someone might genuinely ask about this codebase, each
mapped to the file(s) that actually answer it.

Three rules were followed while writing it, because a benchmark that flatters
the system it measures is worse than none:

1. **Questions were written before the results were looked at.** None was
   adjusted afterwards to make a score improve.
2. **The mix is deliberate.** Roughly a third are phrased around an exact
   identifier, a third are conceptual with no shared vocabulary with the code,
   and a third sit in between. Loading it with identifier questions would
   flatter keyword search; loading it with conceptual ones would flatter the
   embeddings.
3. **Ground truth is the file that answers the question**, not the file that
   happens to rank well. Several answers live in more than one file, and all of
   them are listed.

Labels are paths, not line numbers, so ordinary edits do not invalidate them.
The harness checks every expected path still exists in the index and refuses to
report scores if any is missing -- a stale benchmark silently scoring zero
would be worse than a loud failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class QuestionStyle(StrEnum):
    """How the question is phrased, so results can be broken down by style."""

    # Names a symbol, file or constant that appears verbatim in the code.
    identifier = "identifier"
    # Describes a behaviour in words the code does not use.
    conceptual = "conceptual"
    # Natural phrasing that happens to share some vocabulary with the code.
    mixed = "mixed"


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    query: str
    expected_files: frozenset[str]
    style: QuestionStyle
    # Why these files are the answer. Recorded so a disputed label can be
    # argued about on the merits rather than silently changed to fit a score.
    rationale: str


def _q(
    id: str, query: str, files: list[str], style: QuestionStyle, rationale: str
) -> Question:
    return Question(id, query, frozenset(files), style, rationale)


DEV_BENCHMARK: list[Question] = [
    # ---------- identifier-phrased ----------
    _q(
        "id-01",
        "chunk_source",
        ["backend/app/ingestion/chunker.py"],
        QuestionStyle.identifier,
        "chunk_source is defined here and nowhere else.",
    ),
    _q(
        "id-02",
        "is_secret_path",
        ["backend/app/ingestion/filters.py"],
        QuestionStyle.identifier,
        "The function that decides whether a path is secret-bearing.",
    ),
    _q(
        "id-03",
        "reciprocal_rank_fusion",
        ["backend/app/rag/fusion.py"],
        QuestionStyle.identifier,
        "The fusion implementation.",
    ),
    _q(
        "id-04",
        "create_session_token",
        ["backend/app/core/security.py"],
        QuestionStyle.identifier,
        "Session signing lives in core/security.py.",
    ),
    _q(
        "id-05",
        "github_callback",
        ["backend/app/routes/auth.py"],
        QuestionStyle.identifier,
        "The OAuth callback handler.",
    ),
    _q(
        "id-06",
        "OllamaEmbedder",
        ["backend/app/llm/ollama.py"],
        QuestionStyle.identifier,
        "The embedding client class. Note camelCase is not split by the "
        "english text search configuration, so keyword search may miss this.",
    ),
    _q(
        "id-07",
        "session_scope",
        ["backend/app/core/database.py"],
        QuestionStyle.identifier,
        "The transactional context manager.",
    ),
    _q(
        "id-08",
        "EMBEDDING_DIMENSIONS setting",
        ["backend/app/core/config.py"],
        QuestionStyle.identifier,
        "All settings are declared in one Settings object.",
    ),
    _q(
        "id-09",
        "enqueue_index_repository",
        ["backend/app/queue/base.py", "backend/app/queue/rq_backend.py"],
        QuestionStyle.identifier,
        "Declared on the protocol, implemented by the RQ backend.",
    ),
    # ---------- conceptual ----------
    _q(
        "con-01",
        "how do I stop credentials from being fed to the language model",
        ["backend/app/ingestion/filters.py"],
        QuestionStyle.conceptual,
        "Secret exclusion during ingestion is the control that prevents this.",
    ),
    _q(
        "con-02",
        "what stops one customer seeing another customer's data",
        [
            "backend/app/rag/vector.py",
            "backend/app/rag/keyword.py",
            "backend/app/routes/repositories.py",
        ],
        QuestionStyle.conceptual,
        "Tenant isolation is enforced by repository_id filtering in both "
        "retrievers and by owner-scoped queries in the routes.",
    ),
    _q(
        "con-03",
        "how does the system avoid repeating expensive work when nothing changed",
        ["backend/app/ingestion/service.py", "backend/app/ingestion/embedder.py"],
        QuestionStyle.conceptual,
        "content_hash reuse in the service, and the pending-work query in the "
        "embedder.",
    ),
    _q(
        "con-04",
        "what happens if the machine learning service is switched off",
        ["backend/app/llm/ollama.py", "backend/app/rag/retriever.py"],
        QuestionStyle.conceptual,
        "The client raises an upstream error; the retriever degrades to "
        "keyword-only and records a note.",
    ),
    _q(
        "con-05",
        "how is long running work kept off the web request path",
        [
            "backend/app/queue/rq_backend.py",
            "backend/app/workers/ingestion.py",
            "backend/app/routes/repositories.py",
        ],
        QuestionStyle.conceptual,
        "Queue, worker, and the 202-returning endpoint.",
    ),
    _q(
        "con-06",
        "how does the application avoid leaking internal details in failures",
        ["backend/app/core/errors.py"],
        QuestionStyle.conceptual,
        "The error envelope and the unexpected-exception handler.",
    ),
    _q(
        "con-07",
        "how can a request be traced through the logs",
        ["backend/app/core/middleware.py", "backend/app/core/logging.py"],
        QuestionStyle.conceptual,
        "Trace id binding in middleware, contextvar plumbing in logging.",
    ),
    _q(
        "con-08",
        "why would two pieces of retrieved text be combined into one ranking",
        ["backend/app/rag/fusion.py", "backend/app/rag/retriever.py"],
        QuestionStyle.conceptual,
        "The merge step and the orchestration around it.",
    ),
    _q(
        "con-09",
        "how are very large source files prevented from overwhelming the system",
        ["backend/app/ingestion/filters.py", "backend/app/ingestion/chunker.py"],
        QuestionStyle.conceptual,
        "A size ceiling in filters; oversized units split into fragments in "
        "the chunker.",
    ),
    _q(
        "con-10",
        "what protects against a malicious archive escaping its directory",
        ["backend/app/ingestion/fetcher.py"],
        QuestionStyle.conceptual,
        "Tar extraction uses filter='data' and bounds the expanded size.",
    ),
    # ---------- mixed ----------
    _q(
        "mix-01",
        "where is the OAuth callback handled",
        ["backend/app/routes/auth.py"],
        QuestionStyle.mixed,
        "The query that vector-only search failed at milestone 4.",
    ),
    _q(
        "mix-02",
        "how are GitHub access tokens encrypted before being stored",
        ["backend/app/core/security.py"],
        QuestionStyle.mixed,
        "Fernet encryption of tokens at rest.",
    ),
    _q(
        "mix-03",
        "how are source files split into chunks",
        ["backend/app/ingestion/chunker.py"],
        QuestionStyle.mixed,
        "The chunker.",
    ),
    _q(
        "mix-04",
        "how do I run the background worker",
        ["backend/app/workers/run_worker.py"],
        QuestionStyle.mixed,
        "The worker entrypoint. Hybrid demoted this at milestone 5, which is "
        "part of why it is in the benchmark.",
    ),
    _q(
        "mix-05",
        "which file extensions map to a parser",
        ["backend/app/ingestion/languages.py"],
        QuestionStyle.mixed,
        "The extension-to-grammar mapping.",
    ),
    _q(
        "mix-06",
        "how is the health of the database and cache reported",
        ["backend/app/routes/health.py"],
        QuestionStyle.mixed,
        "The health endpoint and its probes.",
    ),
    _q(
        "mix-07",
        "how does the API know which user is making a request",
        ["backend/app/core/deps.py"],
        QuestionStyle.mixed,
        "current_user resolves the session cookie to a user row.",
    ),
]


# Written before any tuning was attempted and deliberately not consulted while
# retrieval was being changed. The dev set above has been used for tuning and
# is therefore no longer a clean measure of generalisation; this one is the
# honest number. Keep it that way: if you tune against these, they stop being
# held out and a new set is needed.
HELDOUT_BENCHMARK: list[Question] = [
    _q(
        "h-id-01",
        "reciprocal_rank",
        ["backend/eval/metrics.py"],
        QuestionStyle.identifier,
        "The MRR helper.",
    ),
    _q(
        "h-id-02",
        "detect_language",
        ["backend/app/ingestion/languages.py"],
        QuestionStyle.identifier,
        "Extension to grammar mapping.",
    ),
    _q(
        "h-id-03",
        "TraceMiddleware",
        ["backend/app/core/middleware.py"],
        QuestionStyle.identifier,
        "Correlation id middleware.",
    ),
    _q(
        "h-id-04",
        "decrypt_token",
        ["backend/app/core/security.py"],
        QuestionStyle.identifier,
        "Token decryption.",
    ),
    _q(
        "h-id-05",
        "PassthroughReranker",
        ["backend/app/rag/reranker.py"],
        QuestionStyle.identifier,
        "The inert reranker.",
    ),
    # ---------- conceptual ----------
    _q(
        "h-con-01",
        "how does the code decide a file is not worth reading",
        ["backend/app/ingestion/filters.py", "backend/app/ingestion/discovery.py"],
        QuestionStyle.conceptual,
        "Filtering policy, plus the strict decode that rejects binary content.",
    ),
    _q(
        "h-con-02",
        "where is the boundary that keeps untrusted archives from writing anywhere",
        ["backend/app/ingestion/fetcher.py"],
        QuestionStyle.conceptual,
        "Safe tar extraction.",
    ),
    _q(
        "h-con-03",
        "how does a browser session survive between page loads",
        ["backend/app/core/security.py", "backend/app/routes/auth.py"],
        QuestionStyle.conceptual,
        "Signed cookie minted at the callback and verified per request.",
    ),
    _q(
        "h-con-04",
        "what decides how many pieces of text go to the language model",
        ["backend/app/rag/retriever.py", "backend/app/rag/reranker.py"],
        QuestionStyle.conceptual,
        "Candidate limit and the narrowing step.",
    ),
    _q(
        "h-con-05",
        "how would I add support for a new programming language",
        ["backend/app/ingestion/languages.py", "backend/app/ingestion/chunker.py"],
        QuestionStyle.conceptual,
        "Extension mapping plus the node-type sets the chunker walks.",
    ),
    # ---------- mixed ----------
    _q(
        "h-mix-01",
        "how is progress reported while a repository is being indexed",
        ["backend/app/workers/ingestion.py", "backend/app/ingestion/service.py"],
        QuestionStyle.mixed,
        "Progress callback plumbing and the job row updates.",
    ),
    _q(
        "h-mix-02",
        "how are database migrations applied",
        ["backend/migrations/env.py", "docs/database.md"],
        QuestionStyle.mixed,
        "Alembic environment and the documented workflow.",
    ),
    _q(
        "h-mix-03",
        "what is stored about each retrieved chunk",
        ["backend/app/models/chunk.py", "backend/app/rag/types.py"],
        QuestionStyle.mixed,
        "The chunk table and the retrieval result type.",
    ),
    _q(
        "h-mix-04",
        "how does the frontend keep the session cookie out of browser code",
        ["frontend/src/lib/session.ts"],
        QuestionStyle.mixed,
        "Server-side cookie forwarding.",
    ),
]

# Kept for callers that just want the tuning set.
BENCHMARK = DEV_BENCHMARK

SETS: dict[str, list[Question]] = {
    "dev": DEV_BENCHMARK,
    "heldout": HELDOUT_BENCHMARK,
}


def expected_paths(questions: list[Question] | None = None) -> set[str]:
    """Every path the given questions depend on, for the staleness check."""
    source = questions if questions is not None else DEV_BENCHMARK + HELDOUT_BENCHMARK
    return {path for question in source for path in question.expected_files}
