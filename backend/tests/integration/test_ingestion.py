"""Full indexing runs against a real database.

The download is stubbed -- these tests are about what ends up in Postgres, not
about GitHub -- but discovery, filtering, parsing, chunking and persistence all
run for real.

Needs Postgres running and migrated.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from app.core.database import session_scope
from app.ingestion import service as ingestion_service
from app.ingestion.fetcher import Snapshot
from app.ingestion.service import index_repository
from app.models.chunk import ChunkKind, CodeChunk
from app.models.file import File
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.integration

GITHUB_ID = 900_000_055
COMMIT = "a" * 40
SECOND_COMMIT = "b" * 40


@pytest.fixture
def repository() -> Iterator[Repository]:
    """A connected repository owned by a throwaway user."""
    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))

    with session_scope() as session:
        user = User(github_id=GITHUB_ID, login="ingest-tester")
        session.add(user)
        session.flush()
        repo = Repository(
            user_id=user.id,
            github_id=GITHUB_ID + 1,
            owner="tester",
            name="sample",
            default_branch="main",
        )
        session.add(repo)
        session.flush()
        session.expunge_all()

    with session_scope() as session:
        yield session.execute(
            select(Repository).where(Repository.github_id == GITHUB_ID + 1)
        ).scalar_one()

    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))


@pytest.fixture
def stub_snapshot(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path, str], None]:
    """Replace the GitHub download with a local directory."""

    def install(root: Path, commit: str) -> None:
        @contextmanager
        def fake_fetch(*_args: object, **_kwargs: object) -> Iterator[Snapshot]:
            yield Snapshot(root=root, commit_sha=commit)

        monkeypatch.setattr(ingestion_service, "fetch_snapshot", fake_fetch)

    return install


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


SAMPLE = {
    "src/service.py": (
        "import os\n\n\n"
        "def handle(request):\n"
        '    """Handle a request."""\n'
        "    return request\n\n\n"
        "class Worker:\n"
        "    def run(self):\n"
        "        return handle(None)\n"
    ),
    "src/helpers.ts": "export function helper(a: number) {\n  return a * 2;\n}\n",
    "README.md": "# Sample\n\nSome prose about the project.\n",
    ".env": "API_KEY=super-secret-value\n",
    "node_modules/dep/index.js": "module.exports = 1;\n",
}


def _index(repository: Repository, root: Path, commit: str) -> ingestion_service.IndexResult:
    with session_scope() as session:
        return index_repository(
            session,
            repository_id=repository.id,
            owner=repository.owner,
            name=repository.name,
            ref="main",
            token="gho_irrelevant",
            on_progress=None,
        )


def _chunks(repository_id: uuid.UUID) -> list[CodeChunk]:
    with session_scope() as session:
        return list(
            session.execute(
                select(CodeChunk).where(CodeChunk.repository_id == repository_id)
            ).scalars()
        )


def test_indexing_stores_files_and_chunks(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)

    result = _index(repository, tmp_path, COMMIT)

    assert result.commit_sha == COMMIT
    # Three indexable files: the .env is a secret and node_modules is pruned.
    assert result.files_indexed == 3
    assert result.chunks_created > 0

    with session_scope() as session:
        paths = {
            row.path
            for row in session.execute(
                select(File).where(File.repository_id == repository.id)
            ).scalars()
        }
    assert paths == {"src/service.py", "src/helpers.ts", "README.md"}


def test_the_secret_file_is_neither_stored_nor_chunked(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    """The end-to-end security property, asserted against the database."""
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    _index(repository, tmp_path, COMMIT)

    with session_scope() as session:
        assert (
            session.execute(
                select(func.count())
                .select_from(File)
                .where(File.repository_id == repository.id, File.path == ".env")
            ).scalar_one()
            == 0
        )
        leaked = session.execute(
            select(func.count())
            .select_from(CodeChunk)
            .where(
                CodeChunk.repository_id == repository.id,
                CodeChunk.content.contains("super-secret-value"),
            )
        ).scalar_one()
    assert leaked == 0


def test_chunks_carry_symbols_languages_and_line_numbers(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    _index(repository, tmp_path, COMMIT)

    chunks = _chunks(repository.id)
    symbols = {c.symbol for c in chunks if c.symbol}

    assert "handle" in symbols
    assert "Worker" in symbols
    assert "helper" in symbols
    assert all(c.start_line >= 1 and c.end_line >= c.start_line for c in chunks)
    assert all(len(c.chunk_hash) == 64 for c in chunks)

    # Markdown has no grammar, so it is chunked by size and labelled as such.
    with session_scope() as session:
        readme = session.execute(
            select(File).where(
                File.repository_id == repository.id, File.path == "README.md"
            )
        ).scalar_one()
        assert readme.language is None
        kinds = {
            row.kind
            for row in session.execute(
                select(CodeChunk).where(CodeChunk.file_id == readme.id)
            ).scalars()
        }
    assert kinds == {ChunkKind.fallback}


def test_reindexing_unchanged_content_reuses_rows(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    """The point of content_hash: unchanged files are not re-parsed."""
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    _index(repository, tmp_path, COMMIT)
    first_ids = {c.id for c in _chunks(repository.id)}

    stub_snapshot(tmp_path, SECOND_COMMIT)
    second = _index(repository, tmp_path, SECOND_COMMIT)

    assert second.files_unchanged == 3
    assert second.files_indexed == 0
    # Same chunk rows, not re-created ones -- which is what will make
    # re-embedding skippable in milestone 4.
    assert {c.id for c in _chunks(repository.id)} == first_ids


def test_editing_a_file_replaces_only_its_chunks(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    _index(repository, tmp_path, COMMIT)
    untouched = {c.id for c in _chunks(repository.id) if (c.symbol or "") == "helper"}

    (tmp_path / "src" / "service.py").write_text(
        "def handle(request):\n    return None\n\n\ndef added():\n    return 1\n",
        encoding="utf-8",
    )
    stub_snapshot(tmp_path, SECOND_COMMIT)
    result = _index(repository, tmp_path, SECOND_COMMIT)

    assert result.files_indexed == 1
    assert result.files_unchanged == 2

    symbols = {c.symbol for c in _chunks(repository.id) if c.symbol}
    assert "added" in symbols
    # The class that no longer exists must not linger and be citable.
    assert "Worker" not in symbols
    # The other file's chunks were left alone.
    assert untouched <= {c.id for c in _chunks(repository.id)}


def test_deleted_files_are_removed_from_the_index(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    """A deleted file must not remain retrievable, or answers cite dead code."""
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    _index(repository, tmp_path, COMMIT)

    (tmp_path / "src" / "helpers.ts").unlink()
    stub_snapshot(tmp_path, SECOND_COMMIT)
    _index(repository, tmp_path, SECOND_COMMIT)

    with session_scope() as session:
        paths = {
            row.path
            for row in session.execute(
                select(File).where(File.repository_id == repository.id)
            ).scalars()
        }
    assert "src/helpers.ts" not in paths
    assert "helper" not in {c.symbol for c in _chunks(repository.id) if c.symbol}


def test_chunks_cascade_when_the_repository_is_disconnected(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    _index(repository, tmp_path, COMMIT)
    repository_id = repository.id
    assert _chunks(repository_id)

    with session_scope() as session:
        session.delete(session.get(Repository, repository_id))

    assert _chunks(repository_id) == []
    with session_scope() as session:
        remaining = session.execute(
            select(func.count()).select_from(File).where(File.repository_id == repository_id)
        ).scalar_one()
    assert remaining == 0


def test_progress_is_reported_monotonically(
    repository: Repository, tmp_path: Path, stub_snapshot: Callable[[Path, str], None]
) -> None:
    stub_snapshot(_write_tree(tmp_path, SAMPLE), COMMIT)
    seen: list[tuple[int, str]] = []

    with session_scope() as session:
        index_repository(
            session,
            repository_id=repository.id,
            owner="tester",
            name="sample",
            ref="main",
            token="gho_irrelevant",
            on_progress=lambda pct, stage: seen.append((pct, stage)),
        )

    assert seen[0][0] == 0
    assert seen[-1] == (100, "complete")
    percentages = [pct for pct, _ in seen]
    assert percentages == sorted(percentages), "progress must never go backwards"
