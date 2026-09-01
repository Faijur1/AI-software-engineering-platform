"""Walk an extracted repository tree and decide what to index.

Separate from filtering so the two can be tested independently: discovery is
about traversing a real filesystem safely, filtering is about policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.ingestion.filters import EXCLUDED_DIRECTORIES, SkipReason, classify
from app.ingestion.languages import detect_language

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """A file that passed filtering, with its content already read."""

    path: str
    language: str | None
    content: str
    size_bytes: int


@dataclass(slots=True)
class DiscoveryReport:
    """What was indexed and what was skipped, with counts by reason.

    Skips are counted rather than discarded: "we indexed 412 of 1,908 files"
    is a fact a user can act on, and a spike in ``secret`` or ``not_utf8`` is
    how a filtering bug becomes visible instead of silently shrinking the index.

    ``pruned_directories`` is counted separately from ``skipped`` on purpose.
    Excluded directories are never descended into -- counting the files inside
    ``node_modules`` would mean walking it, which is exactly the cost pruning
    exists to avoid. So those files are not in ``examined`` either: the report
    says how many trees were pruned, rather than implying a file count nobody
    measured.
    """

    included: list[DiscoveredFile] = field(default_factory=list)
    skipped: Counter[SkipReason] = field(default_factory=Counter)
    pruned_directories: int = 0

    @property
    def examined(self) -> int:
        """Files actually looked at. Excludes anything inside a pruned tree."""
        return len(self.included) + sum(self.skipped.values())


def discover(root: Path) -> DiscoveryReport:
    """Find every indexable file beneath ``root``."""
    report = DiscoveryReport()

    files, report.pruned_directories = _walk(root)

    for entry in files:
        relative = entry.relative_to(root).as_posix()
        try:
            size = entry.stat().st_size
        except OSError:
            # A broken symlink or a file that vanished mid-walk.
            report.skipped[SkipReason.binary] += 1
            continue

        decision = classify(relative, size)
        if not decision.include:
            assert decision.reason is not None
            report.skipped[decision.reason] += 1
            if decision.reason is SkipReason.secret:
                # A security decision, so it is logged rather than merely
                # counted -- the path, never the contents.
                logger.info("ingestion_skipped_secret", path=relative)
            continue

        content = _read_text(entry)
        if content is None:
            report.skipped[SkipReason.not_utf8] += 1
            continue

        report.included.append(
            DiscoveredFile(
                path=relative,
                language=detect_language(relative),
                content=content,
                size_bytes=size,
            )
        )

    logger.info(
        "discovery_complete",
        included=len(report.included),
        examined=report.examined,
        pruned_directories=report.pruned_directories,
        reasons={reason.value: count for reason, count in report.skipped.items()},
    )
    return report


def _walk(root: Path) -> tuple[list[Path], int]:
    """Collect regular files, pruning excluded directories as we descend.

    Returns the files found and how many directory trees were pruned.

    Pruning matters: descending into ``node_modules`` to reject each file
    individually can mean hundreds of thousands of pointless stat calls.

    Symlinks are not followed. A repository can contain a link to ``/etc`` or a
    cycle, and neither belongs in an index.
    """
    found: list[Path] = []
    pruned = 0
    stack = [root]

    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue

        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in EXCLUDED_DIRECTORIES:
                    pruned += 1
                else:
                    stack.append(entry)
            elif entry.is_file():
                found.append(entry)

    return found, pruned


def _read_text(path: Path) -> str | None:
    """Read a file as UTF-8, or return None if it is not decodable text.

    A strict decode is the real binary test. The extension check in filters is
    only a cheap first pass -- plenty of binary files have innocuous names.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None
