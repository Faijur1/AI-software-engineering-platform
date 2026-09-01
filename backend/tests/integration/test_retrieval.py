"""Hybrid retrieval against real Postgres full-text and pgvector.

The embedder is a controlled fake so vector rankings are deterministic; the
full-text side is genuinely Postgres, because its tokenisation is the thing
worth testing and mocking it would test nothing.

Needs Postgres running and migrated.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, select

from app.core.database import session_scope
from app.models.chunk import ChunkKind, CodeChunk
from app.models.file import File
from app.models.repository import Repository
from app.models.user import User
from app.rag import keyword, vector
from app.rag.retriever import retrieve
from app.rag.types import RetrievalMethod

pytestmark = pytest.mark.integration

GITHUB_ID = 900_000_088
DIMENSIONS = 768

# A tiny corpus with a deliberate trap: the identifier `github_callback` appears
# in the handler, and "oauth callback" prose appears in the README. Vector
# search favours the prose; only keyword search finds the identifier.
CORPUS = [
    (
        "backend/routes/auth.py",
        "github_callback",
        "def github_callback(request, code, state):\n"
        "    verify_state(state)\n"
        "    return exchange_code(code)\n",
    ),
    (
        "README.md",
        None,
        "## Authentication\n\nThis project uses OAuth. The callback is handled "
        "by the backend and the browser is redirected onward.\n",
    ),
    (
        "backend/ingestion/chunker.py",
        "chunk_source",
        "def chunk_source(content, language):\n"
        "    parser = get_parser(language)\n"
        "    return walk(parser.parse(content))\n",
    ),
    (
        "backend/ingestion/filters.py",
        "is_secret_path",
        "def is_secret_path(path):\n"
        "    return path.endswith('.pem') or path == '.env'\n",
    ),
]


class ControlledEmbedder:
    """Vectors chosen so the vector ranking is known in advance.

    Each document gets a distinct basis direction; a query is embedded as
    whatever direction the test says it should favour.
    """

    def __init__(self) -> None:
        self.query_direction = 0

    @property
    def model_name(self) -> str:
        return "controlled"

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(self.query_direction) for _ in texts]

    @staticmethod
    def _vector(direction: int) -> list[float]:
        vec = [0.0] * DIMENSIONS
        vec[direction % DIMENSIONS] = 1.0
        return vec


@pytest.fixture
def indexed() -> Iterator[uuid.UUID]:
    """A repository with the corpus above, embedded on distinct directions."""
    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))

    with session_scope() as session:
        user = User(github_id=GITHUB_ID, login="rag-tester")
        session.add(user)
        session.flush()
        repo = Repository(
            user_id=user.id, github_id=GITHUB_ID + 1, owner="t", name="corpus"
        )
        session.add(repo)
        session.flush()

        for index, (path, symbol, content) in enumerate(CORPUS):
            source = File(
                repository_id=repo.id,
                path=path,
                language="python",
                content_hash=hashlib.sha256(path.encode()).hexdigest(),
                commit_sha="c" * 40,
                size_bytes=len(content),
            )
            session.add(source)
            session.flush()
            session.add(
                CodeChunk(
                    file_id=source.id,
                    repository_id=repo.id,
                    content=content,
                    symbol=symbol,
                    kind=ChunkKind.function if symbol else ChunkKind.fallback,
                    start_line=1,
                    end_line=content.count("\n") + 1,
                    chunk_hash=hashlib.sha256(content.encode()).hexdigest(),
                    embedding=ControlledEmbedder._vector(index),
                    embedding_model="controlled",
                )
            )
        repo_id = repo.id

    yield repo_id

    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))


def test_keyword_search_finds_a_snake_case_identifier(indexed: uuid.UUID) -> None:
    """Postgres splits snake_case on underscores, which is what makes this work."""
    with session_scope() as session:
        results = keyword.search(
            session, repository_id=indexed, query_text="github_callback", limit=10
        )

    assert results
    assert results[0].file_path == "backend/routes/auth.py"
    assert results[0].symbol == "github_callback"


def test_a_prose_question_falls_back_to_any_terms(indexed: uuid.UUID) -> None:
    """websearch_to_tsquery ANDs every term, which is far too strict for prose.

    No chunk here contains all of "oauth", "callback" and "handled" plus
    "backend", so the strict query matches nothing. The relaxed retry is what
    keeps the keyword half contributing anything at all to such questions.
    """
    query = "where is the oauth callback handled in the backend"

    with session_scope() as session:
        strict = keyword.build_query(session, query)
        assert strict is not None and " & " in strict

        results = keyword.search(
            session, repository_id=indexed, query_text=query, limit=10
        )

    assert results, "the relaxed retry should still find something"
    assert any(r.file_path == "README.md" for r in results)


def test_relaxation_preserves_phrase_grouping() -> None:
    """Split identifiers must stay adjacent, or precision collapses."""
    assert keyword.relax("'github' <-> 'callback' & 'handl'") == (
        "'github' <-> 'callback' | 'handl'"
    )


def test_a_strict_match_is_not_relaxed_away(indexed: uuid.UUID) -> None:
    """When every term is present, that is the better match and must win."""
    with session_scope() as session:
        results = keyword.search(
            session, repository_id=indexed, query_text="chunk_source language", limit=10
        )

    assert results[0].file_path == "backend/ingestion/chunker.py"


def test_vector_search_returns_nearest_by_cosine(indexed: uuid.UUID) -> None:
    embedder = ControlledEmbedder()
    embedder.query_direction = 2  # the chunker document

    with session_scope() as session:
        results = vector.search(
            session,
            repository_id=indexed,
            query_vector=embedder.embed(["irrelevant"])[0],
            limit=10,
        )

    assert results[0].file_path == "backend/ingestion/chunker.py"
    assert results[0].score == pytest.approx(1.0, abs=1e-6)


def test_hybrid_recovers_the_identifier_vector_search_misses(
    indexed: uuid.UUID,
) -> None:
    """The regression this milestone exists to fix.

    The embedder is pointed at the README, so vector search ranks the prose
    first and never surfaces the handler. Keyword search finds the identifier,
    and fusion must put the handler on top.
    """
    embedder = ControlledEmbedder()
    embedder.query_direction = 1  # the README

    with session_scope() as session:
        vector_only = vector.search(
            session,
            repository_id=indexed,
            query_vector=embedder.embed(["q"])[0],
            limit=10,
        )
        assert vector_only[0].file_path == "README.md"

        result = retrieve(
            session,
            embedder,
            repository_id=indexed,
            query="github_callback",
            limit=4,
        )

    assert result.chunks[0].file_path == "backend/routes/auth.py"
    assert result.chunks[0].symbol == "github_callback"
    assert result.chunks[0].method is RetrievalMethod.both


def test_results_are_scoped_to_one_repository(indexed: uuid.UUID) -> None:
    """Retrieval never spans repositories, whatever the query."""
    with session_scope() as session:
        other_user = User(github_id=GITHUB_ID + 700, login="other")
        session.add(other_user)
        session.flush()
        other_repo = Repository(
            user_id=other_user.id, github_id=GITHUB_ID + 701, owner="o", name="other"
        )
        session.add(other_repo)
        session.flush()
        other_file = File(
            repository_id=other_repo.id,
            path="other/secret.py",
            language="python",
            content_hash="z" * 64,
            commit_sha="d" * 40,
            size_bytes=50,
        )
        session.add(other_file)
        session.flush()
        session.add(
            CodeChunk(
                file_id=other_file.id,
                repository_id=other_repo.id,
                content="def github_callback(): pass  # another user's code",
                symbol="github_callback",
                kind=ChunkKind.function,
                start_line=1,
                end_line=1,
                chunk_hash="q" * 64,
                embedding=ControlledEmbedder._vector(1),
                embedding_model="controlled",
            )
        )
        other_repo_id = other_repo.id

    try:
        embedder = ControlledEmbedder()
        embedder.query_direction = 1

        with session_scope() as session:
            result = retrieve(
                session,
                embedder,
                repository_id=indexed,
                query="github_callback",
                limit=10,
            )

        assert all("other/" not in c.file_path for c in result.chunks)
        assert other_repo_id not in {c.chunk_id for c in result.chunks}
    finally:
        with session_scope() as session:
            session.execute(delete(User).where(User.github_id == GITHUB_ID + 700))


def test_a_stopword_only_query_degrades_to_vector_and_says_so(
    indexed: uuid.UUID,
) -> None:
    """Half-working hybrid search must be visible, not silent."""
    embedder = ControlledEmbedder()

    with session_scope() as session:
        result = retrieve(
            session, embedder, repository_id=indexed, query="the and of", limit=5
        )

    assert result.trace.keyword_candidates == 0
    assert result.chunks, "vector search should still return results"
    assert any("vector-only" in note for note in result.trace.notes)


def test_an_embedding_failure_degrades_to_keyword_and_says_so(
    indexed: uuid.UUID,
) -> None:
    """A stopped Ollama must not take search down entirely."""

    class BrokenEmbedder(ControlledEmbedder):
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("ollama is down")

    with session_scope() as session:
        result = retrieve(
            session,
            BrokenEmbedder(),
            repository_id=indexed,
            query="github_callback",
            limit=5,
        )

    assert result.trace.vector_candidates == 0
    assert result.chunks[0].file_path == "backend/routes/auth.py"
    assert all(c.method is RetrievalMethod.keyword for c in result.chunks)
    assert any("keyword-only" in note for note in result.trace.notes)


def test_unembedded_chunks_are_excluded_not_treated_as_distant(
    indexed: uuid.UUID,
) -> None:
    """A partial index must return fewer results, never wrong ones."""
    with session_scope() as session:
        for chunk in session.execute(
            select(CodeChunk).where(CodeChunk.repository_id == indexed)
        ).scalars():
            chunk.embedding = None

    embedder = ControlledEmbedder()
    with session_scope() as session:
        results = vector.search(
            session,
            repository_id=indexed,
            query_vector=embedder.embed(["q"])[0],
            limit=10,
        )

    assert results == []


def test_the_trace_reports_what_each_retriever_contributed(
    indexed: uuid.UUID,
) -> None:
    embedder = ControlledEmbedder()

    with session_scope() as session:
        result = retrieve(
            session, embedder, repository_id=indexed, query="github_callback", limit=2
        )

    assert result.trace.vector_candidates == len(CORPUS)
    assert result.trace.keyword_candidates >= 1
    assert result.trace.returned == 2
    assert result.trace.fused_candidates >= result.trace.returned
    # The reranker is inert and says so.
    assert result.reranker_is_passthrough is True
    assert all(c.rerank_score is None for c in result.chunks)


def test_lexemes_that_stem_twice_still_match(indexed: uuid.UUID) -> None:
    """Regression: the rendered tsquery must not be re-normalised.

    Feeding a rendered tsquery back through ``to_tsquery`` stems the lexemes a
    second time. ``something_else`` renders as ``'someth' <-> 'els'`` and comes
    back as ``'someth' <-> 'el'``, which matches nothing -- a silent recall
    loss affecting only terms whose stem stems again, which is exactly the kind
    of bug that hides until a benchmark measures it.
    """
    with session_scope() as session:
        source = session.execute(
            select(File).where(File.repository_id == indexed)
        ).scalars().first()
        assert source is not None
        session.add(
            CodeChunk(
                file_id=source.id,
                repository_id=indexed,
                content="def something_else(y):\n    return y\n",
                symbol="something_else",
                kind=ChunkKind.function,
                start_line=90,
                end_line=91,
                chunk_hash="e" * 64,
                embedding=ControlledEmbedder._vector(0),
                embedding_model="controlled",
            )
        )

    with session_scope() as session:
        results = keyword.search(
            session, repository_id=indexed, query_text="something_else", limit=10
        )

    assert results, "a doubly-stemming identifier must still be findable"
    assert results[0].symbol == "something_else"


def test_the_inspector_sees_every_candidate_not_just_the_chosen(
    indexed: uuid.UUID,
) -> None:
    """The point of the inspector: explain why the right chunk was not chosen.

    A result set containing only the winners cannot answer that question, so
    retrieval reports the full fused list with selection marked.
    """
    embedder = ControlledEmbedder()

    with session_scope() as session:
        result = retrieve(
            session,
            embedder,
            repository_id=indexed,
            query="github_callback",
            limit=2,
        )

    assert len(result.chunks) == 2
    assert len(result.candidates) == result.trace.fused_candidates
    assert len(result.candidates) > len(result.chunks)

    selected = [c for c in result.candidates if c.selected]
    assert [c.chunk_id for c in selected] == [c.chunk_id for c in result.chunks]


def test_candidates_are_ordered_by_the_reranker_that_ran(
    indexed: uuid.UUID,
) -> None:
    """Rank shown must be the rank the pipeline produced, not fusion order."""
    from app.rag.reranker import RoleWeightedReranker

    embedder = ControlledEmbedder()

    with session_scope() as session:
        result = retrieve(
            session,
            embedder,
            repository_id=indexed,
            query="github_callback",
            limit=2,
            reranker=RoleWeightedReranker(),
        )

    scores = [c.rerank_score for c in result.candidates]
    assert all(s is not None for s in scores)
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]
    # And the top of that order is exactly what was selected.
    assert result.candidates[0].chunk_id == result.chunks[0].chunk_id


def test_a_demoted_candidate_is_still_visible_with_its_scores(
    indexed: uuid.UUID,
) -> None:
    """Role weighting demotes prose; the inspector must still show it, and show
    the fused score it would have had."""
    from app.rag.reranker import RoleWeightedReranker

    embedder = ControlledEmbedder()
    embedder.query_direction = 1  # the README

    with session_scope() as session:
        result = retrieve(
            session,
            embedder,
            repository_id=indexed,
            query="oauth callback",
            limit=1,
            reranker=RoleWeightedReranker(),
        )

    readme = next(c for c in result.candidates if c.file_path == "README.md")
    # Present, scored by both stages, and the demotion is legible.
    assert readme.rerank_score is not None
    assert readme.rerank_score < readme.fused_score
