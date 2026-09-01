"""Mapping from file path to tree-sitter language.

Extension-based rather than content-sniffing: it is predictable, cheap, and
wrong only in cases (a ``.h`` that is really C++) where the parse still
succeeds well enough to chunk.

A language appears here only if a grammar is actually loadable, checked at
import. Claiming support the parser cannot deliver would surface as a runtime
failure mid-index rather than as a clean fallback.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from tree_sitter import Parser
from tree_sitter_language_pack import get_parser

# Extension -> grammar name. Restricted to languages this project can actually
# chunk meaningfully; adding one is a one-line change plus a chunker test.
_BY_EXTENSION: Final[dict[str, str]] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
}

# Filenames with no useful extension.
_BY_FILENAME: Final[dict[str, str]] = {
    "Dockerfile": "dockerfile",
    "Makefile": "make",
}


def detect_language(path: str) -> str | None:
    """Return the grammar name for ``path``, or None if none applies.

    None is not a failure: it routes the file to size-based fallback chunking,
    which is a deliberate, measured degradation (ADR-002).
    """
    name = path.rsplit("/", 1)[-1]
    if name in _BY_FILENAME:
        return _BY_FILENAME[name]
    dot = name.rfind(".")
    if dot <= 0:
        # Leading-dot files (".gitignore") have no extension, only a name.
        return None
    return _BY_EXTENSION.get(name[dot:].lower())


@lru_cache(maxsize=64)
def get_language_parser(language: str) -> Parser | None:
    """Return a parser for ``language``, or None if the grammar is unavailable.

    Cached: parser construction loads a native grammar, and indexing a
    repository asks for the same handful of languages thousands of times.
    """
    try:
        return get_parser(language)
    except Exception:
        # A grammar missing from the installed pack must degrade to fallback
        # chunking, never abort the index.
        return None


def supported_languages() -> frozenset[str]:
    return frozenset(_BY_EXTENSION.values()) | frozenset(_BY_FILENAME.values())
