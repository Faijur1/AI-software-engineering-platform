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


BENCHMARK: list[Question] = [
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


def expected_paths() -> set[str]:
    """Every path any question depends on, for the staleness check."""
    return {path for question in BENCHMARK for path in question.expected_files}
