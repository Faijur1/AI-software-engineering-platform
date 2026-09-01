"""Walking a real repository tree."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.ingestion.discovery import discover
from app.ingestion.filters import SkipReason


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small repository resembling a real one, including things to exclude."""

    def write(relative: str, content: str | bytes = "x = 1\n") -> None:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    write("src/main.py", "def main():\n    return 1\n")
    write("src/util.ts", "export const a = 1;\n")
    write("README.md", "# Title\n\nProse.\n")
    write(".env", "SECRET_KEY=hunter2\n")
    write(".env.example", "SECRET_KEY=replace_me\n")
    write("node_modules/react/index.js", "module.exports = {};\n")
    write("node_modules/react/deep/nested/more.js", "x\n")
    write("package-lock.json", "{}\n")
    write("assets/logo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    write("src/empty.py", "")
    write("src/binary_named_as_source.py", b"\xff\xfe\x00\x01\x02binary")
    return tmp_path


def test_source_files_are_discovered_with_their_language(tree: Path) -> None:
    report = discover(tree)
    by_path = {f.path: f for f in report.included}

    assert by_path["src/main.py"].language == "python"
    assert by_path["src/util.ts"].language == "typescript"
    # No grammar: routed to fallback chunking, which is a deliberate degradation.
    assert by_path["README.md"].language is None
    assert "def main" in by_path["src/main.py"].content


def test_secrets_are_excluded_and_placeholders_are_not(tree: Path) -> None:
    paths = {f.path for f in discover(tree).included}

    assert ".env" not in paths
    assert ".env.example" in paths


def test_no_included_file_contains_the_secret_value(tree: Path) -> None:
    """The end-to-end property: the real secret never enters the pipeline."""
    assert all("hunter2" not in f.content for f in discover(tree).included)


def test_excluded_directories_are_pruned_entirely(tree: Path) -> None:
    """node_modules is never descended into, so its files are never examined."""
    report = discover(tree)

    assert not any(f.path.startswith("node_modules/") for f in report.included)
    assert report.pruned_directories == 1
    # Not counted as skipped files: nothing inside was ever looked at, and
    # claiming a file count here would be claiming a number never measured.
    assert report.skipped[SkipReason.excluded_directory] == 0


def test_binary_content_is_rejected_even_with_a_source_extension(tree: Path) -> None:
    """The extension list is a cheap first pass; the decode is the real test."""
    paths = {f.path for f in discover(tree).included}

    assert "src/binary_named_as_source.py" not in paths
    assert discover(tree).skipped[SkipReason.not_utf8] == 1


def test_every_examined_file_is_either_included_or_counted(tree: Path) -> None:
    """Nothing is silently dropped: examined files all land in one bucket."""
    report = discover(tree)
    on_disk_outside_pruned = sum(
        1
        for p in tree.rglob("*")
        if p.is_file() and "node_modules" not in p.relative_to(tree).parts
    )

    assert report.examined == on_disk_outside_pruned
    assert len(report.included) + sum(report.skipped.values()) == report.examined


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    """A repository can link to /etc, or to itself. Neither belongs in an index."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")

    outside = tmp_path.parent / "outside_the_repo"
    outside.mkdir(exist_ok=True)
    (outside / "secret.py").write_text("leaked = True\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    (tmp_path / "self").symlink_to(tmp_path, target_is_directory=True)

    report = discover(tmp_path)

    assert {f.path for f in report.included} == {"src/real.py"}


def test_empty_tree_is_not_an_error(tmp_path: Path) -> None:
    report = discover(tmp_path)

    assert report.included == []
    assert report.examined == 0
