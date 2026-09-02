"""Which files are indexed, and which are not.

Two kinds of exclusion, kept deliberately separate because they carry different
weight (docs/rag.md):

*Noise* exclusions are about cost and retrieval quality -- vendored code,
build output, lockfiles. Getting one wrong wastes tokens.

*Secret* exclusions are a *security control*. Those files must never be
embedded, never reach the LLM, and never appear in a citation. Getting one
wrong leaks a credential. They are checked first, tested separately, and a
match is reported so it can be logged as a policy decision rather than
silently skipped.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# Directory names excluded anywhere in the tree. Matched per path segment, so
# "src/node_modules/x" is excluded but "src/my_dist_helper.py" is not.
EXCLUDED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "bower_components", "vendor", "third_party",
        "dist", "build", "out", "target", "bin", "obj",
        ".next", ".nuxt", ".svelte-kit", ".output",
        "__pycache__", ".venv", "venv", "env", ".tox", ".nox", "site-packages",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
        "coverage", "htmlcov", ".coverage",
        ".idea", ".vscode", ".gradle", "Pods", "DerivedData",
    }
)

# Files whose presence is machine-generated noise: large, uninformative, and
# never what a developer means when they ask a question about the code.
EXCLUDED_FILENAMES: Final[frozenset[str]] = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
        "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock",
        "composer.lock", "Gemfile.lock", "go.sum", "packages.lock.json",
    }
)

EXCLUDED_GLOBS: Final[tuple[str, ...]] = (
    "*.min.js", "*.min.css", "*.map",
    "*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*.g.dart", "*.generated.*",
    "*.snap", "*.tsbuildinfo",
)

# Anything not plausibly source text. Checked by extension before reading, so a
# 200 MB binary is never loaded into memory to discover it is binary.
BINARY_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".avif",
        ".svg", ".pdf", ".psd", ".ai", ".sketch", ".fig",
        ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm", ".flac", ".ogg",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".lib", ".class",
        ".pyc", ".pyo", ".pyd", ".wasm", ".node",
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
        ".db", ".sqlite", ".sqlite3", ".mdb", ".parquet", ".avro",
        ".lock", ".pack", ".idx", ".DS_Store",
    }
)

# --- Security control. Treat every addition here as a security change. -------

SECRET_FILENAMES: Final[frozenset[str]] = frozenset(
    {
        ".env", ".envrc", ".netrc", "_netrc",
        "credentials", "credentials.json", "client_secret.json",
        "service-account.json", "serviceAccount.json",
        "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
        ".htpasswd", ".pgpass", "secrets.yml", "secrets.yaml",
        "terraform.tfvars", ".npmrc", ".pypirc",
    }
)

SECRET_GLOBS: Final[tuple[str, ...]] = (
    ".env.*",           # .env.local, .env.production
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.asc", "*.gpg",
    "*id_rsa*", "*id_ed25519*",
    "*secret*.json", "*secrets*.json", "*credentials*.json",
    "*.kdbx",
)

# ".env.example" and friends are placeholders by convention and are useful
# documentation of what configuration exists. Allowed back in explicitly.
SECRET_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {".env.example", ".env.sample", ".env.template", ".env.defaults"}
)

# A file larger than this is excluded whatever its extension: past roughly this
# size a source file is generated, vendored, or data.
MAX_FILE_BYTES: Final[int] = 512 * 1024


class SkipReason(StrEnum):
    """Why a file was not indexed. Reported, never silent."""

    secret = "secret"
    excluded_directory = "excluded_directory"
    excluded_filename = "excluded_filename"
    generated = "generated"
    binary = "binary"
    too_large = "too_large"
    empty = "empty"
    not_utf8 = "not_utf8"


@dataclass(frozen=True, slots=True)
class FilterDecision:
    include: bool
    reason: SkipReason | None = None


INCLUDE: Final = FilterDecision(include=True)


def _segments(path: str) -> list[str]:
    return [s for s in path.replace("\\", "/").split("/") if s and s != "."]


def is_secret_path(path: str) -> bool:
    """Whether the path is one that must never be indexed.

    Separate and public so it can be asserted directly by the security tests,
    independently of the rest of the filtering pipeline.
    """
    name = _segments(path)[-1] if _segments(path) else path
    if name in SECRET_ALLOWLIST:
        return False
    if name in SECRET_FILENAMES:
        return True
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in SECRET_GLOBS)


def classify(
    path: str, size_bytes: int, *, extra_excluded_dirs: frozenset[str] = frozenset()
) -> FilterDecision:
    """Decide whether ``path`` should be indexed, and if not, why.

    Order matters: the security check runs before every convenience check, so
    no future reordering of the cheap exclusions can let a secret through.

    ``extra_excluded_dirs`` adds deployment-specific directory names on top of
    the built-in list. It is a parameter rather than a constant because the
    names that belong in it are not universal: ``eval`` holds this project's
    benchmark, but in another repository it is ordinary source, and silently
    dropping it there would shrink someone's index for no reason they could
    see.
    """
    segments = _segments(path)
    if not segments:
        return FilterDecision(False, SkipReason.excluded_filename)
    name = segments[-1]

    if is_secret_path(path):
        return FilterDecision(False, SkipReason.secret)

    excluded_dirs = EXCLUDED_DIRECTORIES | extra_excluded_dirs
    if any(seg in excluded_dirs for seg in segments[:-1]):
        return FilterDecision(False, SkipReason.excluded_directory)

    if name in EXCLUDED_FILENAMES:
        return FilterDecision(False, SkipReason.excluded_filename)

    lowered = name.lower()
    if any(fnmatch.fnmatch(lowered, g) for g in EXCLUDED_GLOBS):
        return FilterDecision(False, SkipReason.generated)

    dot = lowered.rfind(".")
    if dot > 0 and lowered[dot:] in BINARY_EXTENSIONS:
        return FilterDecision(False, SkipReason.binary)

    if size_bytes == 0:
        return FilterDecision(False, SkipReason.empty)
    if size_bytes > MAX_FILE_BYTES:
        return FilterDecision(False, SkipReason.too_large)

    return INCLUDE
