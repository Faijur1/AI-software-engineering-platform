"""Fetch a repository snapshot from GitHub.

Uses the tarball endpoint rather than ``git clone``. Three reasons:

1. **No credential in a process argument list.** Cloning a private repository
   over HTTPS means putting the token in the URL or in ``-c http.extraHeader``,
   both of which are visible to anything that can read ``/proc`` or run ``ps``.
   The tarball is fetched with an ``Authorization`` header instead.
2. **No dependency on a ``git`` binary** being present and on PATH in whatever
   container the worker eventually runs in.
3. Indexing only ever needs the tree at one commit. Nothing here reads history,
   so a snapshot is the whole requirement, and incremental re-indexing keys off
   per-file content hashes rather than off a git diff.

The trade-off is that a re-index re-downloads the whole tree instead of
fetching a delta. At Stage 1 repository sizes that is seconds, and the file
hash comparison still avoids the expensive part -- re-parsing and, from
milestone 4, re-embedding.
"""

from __future__ import annotations

import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

from app.core.errors import ExternalServiceError, NotFoundError
from app.core.logging import get_logger
from app.services.github import API_BASE, auth_headers

logger = get_logger(__name__)

# Generous enough for a real project, small enough that a runaway download
# cannot fill the disk. Enforced while streaming, not after.
MAX_ARCHIVE_BYTES: Final = 250 * 1024 * 1024
# Total extracted size, which a compressed archive can vastly exceed.
MAX_EXTRACTED_BYTES: Final = 1024 * 1024 * 1024
_DOWNLOAD_TIMEOUT: Final = httpx.Timeout(120.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An extracted repository tree at a known commit."""

    root: Path
    commit_sha: str


def resolve_commit(token: str, owner: str, name: str, ref: str) -> str:
    """Resolve a branch name to the full commit SHA it currently points at.

    Recorded on every file row, so a citation can always be traced back to the
    exact revision it was produced from.
    """
    url = f"{API_BASE}/repos/{owner}/{name}/commits/{ref}"
    headers = auth_headers(token)
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ExternalServiceError("GitHub could not be reached") from exc

    if response.status_code == 404:
        raise NotFoundError("Repository or branch not found")
    if response.status_code >= 400:
        raise ExternalServiceError(f"GitHub returned {response.status_code}")

    payload: dict[str, Any] = response.json()
    sha = payload.get("sha")
    if not isinstance(sha, str) or len(sha) != 40:
        raise ExternalServiceError("GitHub returned no commit SHA")
    return sha


@contextmanager
def fetch_snapshot(token: str, owner: str, name: str, ref: str) -> Iterator[Snapshot]:
    """Download and extract the repository at ``ref`` into a temporary tree.

    The directory is removed when the context exits, including on failure, so a
    failed index leaves nothing behind on disk.
    """
    commit_sha = resolve_commit(token, owner, name, ref)

    with tempfile.TemporaryDirectory(prefix="aisep-ingest-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "snapshot.tar.gz"
        _download(token, owner, name, commit_sha, archive)

        extract_root = tmp_path / "tree"
        extract_root.mkdir()
        _extract(archive, extract_root)
        # The archive wraps everything in one "owner-repo-sha" directory.
        entries = list(extract_root.iterdir())
        root = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root

        logger.info(
            "snapshot_ready", repository=f"{owner}/{name}", commit=commit_sha[:12]
        )
        yield Snapshot(root=root, commit_sha=commit_sha)


def _download(token: str, owner: str, name: str, ref: str, destination: Path) -> None:
    """Stream the tarball to disk, enforcing the size cap as it arrives."""
    url = f"{API_BASE}/repos/{owner}/{name}/tarball/{ref}"
    headers = auth_headers(token)

    written = 0
    try:
        with (
            httpx.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            if response.status_code == 404:
                raise NotFoundError("Repository or branch not found")
            if response.status_code >= 400:
                raise ExternalServiceError(f"GitHub returned {response.status_code}")

            with destination.open("wb") as handle:
                for block in response.iter_bytes(chunk_size=1 << 16):
                    written += len(block)
                    if written > MAX_ARCHIVE_BYTES:
                        # Abort mid-stream rather than discovering the size
                        # after the disk is already full.
                        raise ExternalServiceError(
                            "Repository archive exceeds the maximum supported size"
                        )
                    handle.write(block)
    except httpx.HTTPError as exc:
        logger.warning("snapshot_download_failed", error=type(exc).__name__)
        raise ExternalServiceError("GitHub could not be reached") from exc


def _extract(archive: Path, destination: Path) -> None:
    """Extract the archive safely into ``destination``.

    Repository content is untrusted input (docs/security.md), and a tar archive
    can name absolute paths, ``..`` traversals, symlinks pointing outside the
    tree, and devices. ``filter="data"`` rejects all of those; the size ceiling
    covers the remaining case of a small archive that expands enormously.
    """
    with tarfile.open(archive, mode="r:gz") as tar:
        # getmembers() reads the index once and caches it. Iterating the
        # TarFile directly would consume the stream, leaving extractall with
        # nothing to write.
        members = tar.getmembers()
        total = sum(m.size for m in members if m.isfile())
        if total > MAX_EXTRACTED_BYTES:
            raise ExternalServiceError("Repository expands beyond the maximum supported size")
        tar.extractall(destination, members=members, filter="data")
