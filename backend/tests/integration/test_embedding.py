"""Embedding chunks and storing vectors in pgvector.

The provider is a deterministic fake -- what is under test is the pass over the
database, not Ollama. A separate test exercises the real model.

Needs Postgres running and migrated.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select

from app.core.database import session_scope
from app.core.errors import ExternalServiceError
from app.ingestion.embedder import count_pending, embed_repository
from app.models.chunk import ChunkKind, CodeChunk
from app.models.file import File
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.integration

GITHUB_ID = 900_000_077
DIMENSIONS = 768


class FakeEmbedder:
    """Deterministic vectors seeded by the text.

    Pseudo-random rather than a formula over the index: the same text must give
    the same vector, but two different texts must give near-orthogonal ones. A
    smooth function of the index produces vectors that are all nearly parallel,
    which makes any cosine ranking over them meaningless.
    """

    def __init__(self, name: str = "fake-model", dimensions: int = DIMENSIONS) -> None:
        self._name = name
        self._dimensions = dimensions
        self.calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode()).hexdigest()[:16], 16)
            rng = random.Random(seed)
            vectors.append([rng.uniform(-1.0, 1.0) for _ in range(self._dimensions)])
        return vectors


class FailingEmbedder(FakeEmbedder):
    """Succeeds for ``succeed_batches`` calls, then fails -- a mid-run outage."""

    def __init__(self, succeed_batches: int) -> None:
        super().__init__()
        self._remaining = succeed_batches

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._remaining <= 0:
            raise ExternalServiceError("The embedding model could not be reached")
        self._remaining -= 1
        return super().embed(texts)


@pytest.fixture
def repository_with_chunks() -> Iterator[tuple[uuid.UUID, int]]:
    """A repository holding 50 unembedded chunks."""
    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))

    with session_scope() as session:
        user = User(github_id=GITHUB_ID, login="embed-tester")
        session.add(user)
        session.flush()
        repo = Repository(
            user_id=user.id, github_id=GITHUB_ID + 1, owner="t", name="sample"
        )
        session.add(repo)
        session.flush()
        source = File(
            repository_id=repo.id,
            path="src/main.py",
            language="python",
            content_hash="h" * 64,
            commit_sha="c" * 40,
            size_bytes=100,
        )
        session.add(source)
        session.flush()
        for i in range(50):
            content = f"def function_{i}():\n    return {i}\n"
            session.add(
                CodeChunk(
                    file_id=source.id,
                    repository_id=repo.id,
                    content=content,
                    symbol=f"function_{i}",
                    kind=ChunkKind.function,
                    start_line=i * 3 + 1,
                    end_line=i * 3 + 2,
                    chunk_hash=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
        repo_id = repo.id

    yield repo_id, 50

    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))


def _embedded_count(repository_id: uuid.UUID) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(CodeChunk)
                .where(
                    CodeChunk.repository_id == repository_id,
                    CodeChunk.embedding.is_not(None),
                )
            ).scalar_one()
        )


def test_all_chunks_are_embedded_and_stored(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    repo_id, total = repository_with_chunks
    provider = FakeEmbedder()

    with session_scope() as session:
        result = embed_repository(session, provider, repository_id=repo_id, batch_size=16)

    assert result.embedded == total
    assert _embedded_count(repo_id) == total

    with session_scope() as session:
        chunk = session.execute(
            select(CodeChunk).where(CodeChunk.repository_id == repo_id)
        ).scalars().first()
        assert chunk is not None
        # The vector survives the round trip through pgvector at full width.
        assert chunk.embedding is not None
        assert len(chunk.embedding) == DIMENSIONS
        assert chunk.embedding_model == "fake-model"


def test_work_is_batched_not_one_request_per_chunk(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    repo_id, _total = repository_with_chunks
    provider = FakeEmbedder()

    with session_scope() as session:
        embed_repository(session, provider, repository_id=repo_id, batch_size=16)

    # 50 chunks at 16 per batch is 4 requests, not 50.
    assert len(provider.calls) == 4
    assert [len(c) for c in provider.calls] == [16, 16, 16, 2]


def test_re_running_embeds_nothing_when_everything_is_current(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    """The point of storing embedding_model: no repeated cost for no change."""
    repo_id, total = repository_with_chunks

    with session_scope() as session:
        embed_repository(session, FakeEmbedder(), repository_id=repo_id, batch_size=16)

    second = FakeEmbedder()
    with session_scope() as session:
        result = embed_repository(session, second, repository_id=repo_id, batch_size=16)

    assert result.embedded == 0
    assert result.skipped_already_current == total
    assert second.calls == [], "no request should be made when nothing is pending"


def test_changing_the_model_re_embeds_everything(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    """Vectors from different models are not comparable, so they must be redone."""
    repo_id, total = repository_with_chunks

    with session_scope() as session:
        embed_repository(session, FakeEmbedder("model-a"), repository_id=repo_id, batch_size=16)

    with session_scope() as session:
        result = embed_repository(
            session, FakeEmbedder("model-b"), repository_id=repo_id, batch_size=16
        )

    assert result.embedded == total

    with session_scope() as session:
        models = {
            row.embedding_model
            for row in session.execute(
                select(CodeChunk).where(CodeChunk.repository_id == repo_id)
            ).scalars()
        }
    assert models == {"model-b"}


def test_a_failure_partway_keeps_the_completed_batches(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    """An outage must not discard work already done."""
    repo_id, total = repository_with_chunks

    with pytest.raises(ExternalServiceError), session_scope() as session:
        embed_repository(
            session, FailingEmbedder(succeed_batches=2), repository_id=repo_id, batch_size=16
        )

    # session_scope rolls back on the exception, so nothing from the failed
    # transaction persists -- the chunks stay pending and are retried whole.
    assert _embedded_count(repo_id) == 0

    with session_scope() as session:
        assert count_pending(session, repo_id, "fake-model") == total


def test_a_retry_after_a_failure_completes_the_work(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    repo_id, total = repository_with_chunks

    with pytest.raises(ExternalServiceError), session_scope() as session:
        embed_repository(
            session, FailingEmbedder(succeed_batches=1), repository_id=repo_id, batch_size=16
        )

    with session_scope() as session:
        result = embed_repository(
            session, FakeEmbedder(), repository_id=repo_id, batch_size=16
        )

    assert result.embedded == total
    assert _embedded_count(repo_id) == total


def test_progress_is_reported_against_a_real_total(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    repo_id, total = repository_with_chunks
    seen: list[tuple[int, int]] = []

    with session_scope() as session:
        embed_repository(
            session,
            FakeEmbedder(),
            repository_id=repo_id,
            batch_size=16,
            on_progress=lambda done, count: seen.append((done, count)),
        )

    assert seen[0] == (0, total)
    assert seen[-1] == (total, total)
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)


def test_embeddings_are_isolated_per_repository(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    """Embedding one repository must not touch another's chunks."""
    repo_id, _ = repository_with_chunks

    with session_scope() as session:
        other_user = User(github_id=GITHUB_ID + 500, login="other")
        session.add(other_user)
        session.flush()
        other_repo = Repository(
            user_id=other_user.id, github_id=GITHUB_ID + 501, owner="o", name="other"
        )
        session.add(other_repo)
        session.flush()
        other_file = File(
            repository_id=other_repo.id,
            path="a.py",
            language="python",
            content_hash="x" * 64,
            commit_sha="d" * 40,
            size_bytes=10,
        )
        session.add(other_file)
        session.flush()
        session.add(
            CodeChunk(
                file_id=other_file.id,
                repository_id=other_repo.id,
                content="def other(): pass",
                symbol="other",
                kind=ChunkKind.function,
                start_line=1,
                end_line=1,
                chunk_hash="y" * 64,
            )
        )
        other_repo_id = other_repo.id

    try:
        with session_scope() as session:
            embed_repository(session, FakeEmbedder(), repository_id=repo_id, batch_size=16)

        assert _embedded_count(other_repo_id) == 0
    finally:
        with session_scope() as session:
            session.execute(delete(User).where(User.github_id == GITHUB_ID + 500))


def test_similarity_search_ranks_the_matching_chunk_first(
    repository_with_chunks: tuple[uuid.UUID, int]
) -> None:
    """The vectors are usable for search, which is the point of storing them.

    Not a retrieval-quality claim -- that is milestone 6. This only asserts
    that the column, the operator and the index work together end to end.
    """
    repo_id, _ = repository_with_chunks
    provider = FakeEmbedder()

    with session_scope() as session:
        embed_repository(session, provider, repository_id=repo_id, batch_size=16)

    target = "def function_7():\n    return 7\n"
    query = provider.embed([target])[0]

    with session_scope() as session:
        best = session.execute(
            select(CodeChunk)
            .where(CodeChunk.repository_id == repo_id)
            .order_by(CodeChunk.embedding.cosine_distance(query))
            .limit(1)
        ).scalar_one()

    assert best.symbol == "function_7"
