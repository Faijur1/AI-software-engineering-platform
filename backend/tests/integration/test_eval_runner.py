"""The evaluation harness against a real index.

A small controlled corpus rather than the real repository, so expected scores
are known exactly. Whether the real repository scores well is a finding to
report, not something to assert here -- pinning real scores would make the
suite fail every time retrieval legitimately changed.

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
from eval import runner
from eval.benchmark import Question, QuestionStyle
from eval.runner import StaleBenchmarkError, assert_labels_are_current, run_question

pytestmark = pytest.mark.integration

GITHUB_ID = 900_000_099
DIMENSIONS = 768

CORPUS = {
    "src/target.py": "def find_me(x):\n    return x\n",
    "src/other.py": "def something_else(y):\n    return y\n",
    "docs/notes.md": "Some prose about finding things.\n",
}


class OneDirectionEmbedder:
    """Always returns the same vector, so vector ranking is deterministic."""

    @property
    def model_name(self) -> str:
        return "controlled"

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        vec = [0.0] * DIMENSIONS
        vec[0] = 1.0
        return [vec for _ in texts]


@pytest.fixture
def indexed() -> Iterator[uuid.UUID]:
    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))

    with session_scope() as session:
        user = User(github_id=GITHUB_ID, login="eval-tester")
        session.add(user)
        session.flush()
        repo = Repository(
            user_id=user.id, github_id=GITHUB_ID + 1, owner="t", name="evalcorpus"
        )
        session.add(repo)
        session.flush()

        for position, (path, content) in enumerate(CORPUS.items()):
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
            vec = [0.0] * DIMENSIONS
            # Only the first document sits on the query direction.
            vec[0] = 1.0 if position == 0 else 0.0
            vec[position + 1] = 1.0
            session.add(
                CodeChunk(
                    file_id=source.id,
                    repository_id=repo.id,
                    content=content,
                    symbol=None,
                    kind=ChunkKind.function,
                    start_line=1,
                    end_line=2,
                    chunk_hash=hashlib.sha256(content.encode()).hexdigest(),
                    embedding=vec,
                    embedding_model="controlled",
                )
            )
        repo_id = repo.id

    yield repo_id

    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))


def _question(query: str, expected: set[str], qid: str = "t") -> Question:
    return Question(
        id=qid,
        query=query,
        expected_files=frozenset(expected),
        style=QuestionStyle.identifier,
        rationale="fixture",
    )


def test_a_perfect_answer_scores_one(indexed: uuid.UUID) -> None:
    with session_scope() as session:
        result = run_question(
            session,
            OneDirectionEmbedder(),
            repository_id=indexed,
            question=_question("find_me", {"src/target.py"}),
            use_vector=True,
            use_keyword=True,
        )

    assert result.retrieved[0] == "src/target.py"
    assert result.scores[1]["recall"] == 1.0
    assert result.scores[1]["precision"] == 1.0
    assert result.scores[1]["reciprocal_rank"] == 1.0
    assert result.scores[1]["hit"] == 1.0


def test_a_missed_answer_scores_zero(indexed: uuid.UUID) -> None:
    with session_scope() as session:
        result = run_question(
            session,
            OneDirectionEmbedder(),
            repository_id=indexed,
            question=_question("find_me", {"src/nonexistent.py"}),
            use_vector=True,
            use_keyword=True,
        )

    assert result.scores[10]["recall"] == 0.0
    assert result.scores[10]["hit"] == 0.0


def test_results_are_deduplicated_to_file_granularity(indexed: uuid.UUID) -> None:
    """Two chunks from one file are one piece of evidence, not two.

    Counting them separately would inflate precision for a retriever that
    happens to return several chunks from the same file.
    """
    with session_scope() as session:
        source = session.execute(
            select(File).where(
                File.repository_id == indexed, File.path == "src/target.py"
            )
        ).scalar_one()
        vec = [0.0] * DIMENSIONS
        vec[0] = 1.0
        session.add(
            CodeChunk(
                file_id=source.id,
                repository_id=indexed,
                content="def find_me_too(x):\n    return x\n",
                symbol=None,
                kind=ChunkKind.function,
                start_line=4,
                end_line=5,
                chunk_hash="d" * 64,
                embedding=vec,
                embedding_model="controlled",
            )
        )

    with session_scope() as session:
        result = run_question(
            session,
            OneDirectionEmbedder(),
            repository_id=indexed,
            question=_question("find_me", {"src/target.py"}),
            use_vector=True,
            use_keyword=True,
        )

    assert result.retrieved.count("src/target.py") == 1


def test_ablation_flags_actually_disable_a_retriever(indexed: uuid.UUID) -> None:
    """The comparison is only meaningful if the baselines are really baselines."""
    question = _question("something_else", {"src/other.py"})

    with session_scope() as session:
        keyword_only = run_question(
            session,
            OneDirectionEmbedder(),
            repository_id=indexed,
            question=question,
            use_vector=False,
            use_keyword=True,
        )
        vector_only = run_question(
            session,
            OneDirectionEmbedder(),
            repository_id=indexed,
            question=question,
            use_vector=True,
            use_keyword=False,
        )

    # Keyword finds the identifier; the embedder points everything at target.py.
    assert keyword_only.retrieved[0] == "src/other.py"
    assert vector_only.retrieved[0] == "src/target.py"


def test_a_stale_label_stops_the_run(
    indexed: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently scoring zero on a moved file looks exactly like a regression."""
    monkeypatch.setattr(
        runner, "expected_paths", lambda: {"src/target.py", "src/deleted.py"}
    )

    with (
        session_scope() as session,
        pytest.raises(StaleBenchmarkError, match=r"deleted.py"),
    ):
        assert_labels_are_current(session, indexed)


def test_current_labels_pass_the_staleness_check(
    indexed: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "expected_paths", lambda: {"src/target.py"})

    with session_scope() as session:
        assert_labels_are_current(session, indexed)
