"""Classify an indexed file by what it is: source, test, documentation, config.

Motivated by a measured failure, not a hunch. Every question hybrid retrieval
missed at milestone 6 missed the same way: the prose *about* the code
out-competed the code. Asking for ``OllamaEmbedder`` returned its two test
files; asking how secrets are excluded returned ``docs/security.md``.

That is not surprising in hindsight. A test file names the thing under test
repeatedly and describes it in the words a person would use; a design document
explains a mechanism in exactly the vocabulary of a question about it. Both are
genuinely similar to the query. They are just rarely the answer.

Classification is by path, which is crude but predictable, checkable, and free.
The alternative -- inferring role from content -- would be a model whose
mistakes are much harder to explain.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class FileRole(StrEnum):
    source = "source"
    test = "test"
    docs = "docs"
    config = "config"


_TEST_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {"tests", "test", "__tests__", "spec", "e2e"}
)
_DOC_EXTENSIONS: Final[frozenset[str]] = frozenset({".md", ".rst", ".txt", ".adoc"})
_DOC_DIRECTORIES: Final[frozenset[str]] = frozenset({"docs", "doc"})
_CONFIG_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".lock", ".example"}
)
_CONFIG_DIRECTORIES: Final[frozenset[str]] = frozenset({"migrations"})


def classify_role(path: str) -> FileRole:
    """Return what kind of file ``path`` is.

    Order matters. A file can satisfy several rules -- ``tests/conftest.py`` is
    both a test and Python source, ``docs/adr/*.md`` is both documentation and
    a directory match -- and the first rule that applies wins. Tests are
    checked first because a test *about* configuration is still a test.
    """
    normalised = path.replace("\\", "/").lower()
    segments = normalised.split("/")
    name = segments[-1]

    if any(segment in _TEST_DIRECTORIES for segment in segments[:-1]):
        return FileRole.test
    if name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.tsx")):
        return FileRole.test
    if name.endswith(("_spec.rb", ".spec.ts", ".spec.tsx", ".spec.js")):
        return FileRole.test

    dot = name.rfind(".")
    extension = name[dot:] if dot > 0 else ""

    if extension in _DOC_EXTENSIONS:
        return FileRole.docs
    if any(segment in _DOC_DIRECTORIES for segment in segments[:-1]):
        return FileRole.docs

    if extension in _CONFIG_EXTENSIONS or name.startswith(".env"):
        return FileRole.config
    if any(segment in _CONFIG_DIRECTORIES for segment in segments[:-1]):
        return FileRole.config

    return FileRole.source
