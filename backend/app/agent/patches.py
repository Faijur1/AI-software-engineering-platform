"""Propose, validate and gate code changes.

The agent never edits code in place. It produces a unified diff, which is
applied **only inside the sandbox** for validation, and reaches the working
tree only through a human approval that is a separate, explicit action
(docs/agents.md).

Three separations do the security work here:

1. **Parsing is not applying.** A diff is inspected on the host — enough to
   reject one that names paths outside the workspace — but no file is written
   there. Reading text is safe; writing what a model produced is not.
2. **Applying happens in the container**, against a copy, using a small applier
   shipped in alongside it. The base image has no ``git`` and no ``patch``, and
   with no network nothing can be installed, so the applier is pure Python.
3. **Validation is not approval.** A patch whose tests pass is still
   ``proposed``. Only a person moves it to ``approved``, and the record carries
   who and when.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from app.core.logging import get_logger
from app.sandbox import SandboxResult
from app.sandbox import run as sandbox_run

logger = get_logger(__name__)

# Enough for a real change, small enough that a model looping cannot produce a
# multi-megabyte diff that has to be stored and rendered.
MAX_DIFF_CHARS: Final = 200_000

_FILE_HEADER = re.compile(r"^\+\+\+ (?:b/)?(.+?)(?:\t.*)?$", re.MULTILINE)
_OLD_HEADER = re.compile(r"^--- (?:a/)?(.+?)(?:\t.*)?$", re.MULTILINE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


class PatchRejected(Exception):
    """The diff is unusable or names paths it may not touch."""


@dataclass(slots=True)
class ParsedPatch:
    """A diff that has been checked but not applied."""

    diff: str
    files: list[str] = field(default_factory=list)
    hunks: int = 0


@dataclass(slots=True)
class ValidationResult:
    """What happened when the patch was applied and tested in the sandbox."""

    applied: bool
    tests_passed: bool | None
    output: str
    exit_code: int
    timed_out: bool = False

    @property
    def validated(self) -> bool:
        """True only when it applied *and* the suite passed.

        Never inferred from one of the two: a patch that applies cleanly and
        breaks the tests is not validated, and reporting it as such is exactly
        the failure this gate exists to prevent.
        """
        return self.applied and self.tests_passed is True


def parse_patch(diff: str, *, workspace_files: set[str] | None = None) -> ParsedPatch:
    """Check a unified diff without writing anything.

    Rejects rather than repairs. A diff that cannot be understood is a diff
    that must not be applied, and silently fixing a model's malformed output
    would mean applying something nobody wrote.
    """
    if not diff or not diff.strip():
        raise PatchRejected("The patch is empty.")
    if len(diff) > MAX_DIFF_CHARS:
        raise PatchRejected(
            f"The patch is {len(diff)} characters, over the {MAX_DIFF_CHARS} limit."
        )

    hunks = len(_HUNK.findall(diff))
    if hunks == 0:
        raise PatchRejected(
            "No unified-diff hunks found. Expected @@ -old,count +new,count @@ headers."
        )

    targets = _FILE_HEADER.findall(diff) or _OLD_HEADER.findall(diff)
    if not targets:
        raise PatchRejected("No file headers found. Expected --- and +++ lines.")

    files: list[str] = []
    for raw in targets:
        path = raw.strip()
        if path == "/dev/null":
            continue
        _reject_unsafe_path(path)
        if workspace_files is not None and path not in workspace_files:
            raise PatchRejected(f"'{path}' is not a file in this repository.")
        files.append(path)

    if not files:
        raise PatchRejected("The patch does not name any file to change.")

    return ParsedPatch(diff=diff, files=files, hunks=hunks)


def _reject_unsafe_path(path: str) -> None:
    """Refuse anything that could escape the workspace.

    Checked on the raw string as well as by resolution, because a diff header
    is text the applier will interpret: an absolute path or a ``..`` segment
    must never reach it in the first place.
    """
    normalised = path.replace("\\", "/")
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
        raise PatchRejected(f"'{path}' is an absolute path and cannot be patched.")
    if ".." in Path(normalised).parts:
        raise PatchRejected(f"'{path}' escapes the repository workspace.")


# A pure-Python unified-diff applier, shipped into the container. The base
# image has no git and no patch, and with no network nothing can be installed.
# It re-validates every path itself: this file runs where the diff is actually
# applied, so it must not depend on a check made elsewhere.
_APPLIER: Final = '''\
import sys, os, re

ROOT = os.path.realpath(sys.argv[1])
diff = open(sys.argv[2], encoding="utf-8").read()

def safe(rel):
    target = os.path.realpath(os.path.join(ROOT, rel))
    if target != ROOT and not target.startswith(ROOT + os.sep):
        print("REFUSED: path escapes workspace:", rel)
        sys.exit(2)
    return target

blocks, current = [], None
for line in diff.splitlines():
    if line.startswith("--- "):
        current = {"old": line[4:].strip(), "new": None, "hunks": []}
        blocks.append(current)
    elif line.startswith("+++ ") and current is not None:
        current["new"] = line[4:].strip()
    elif line.startswith("@@") and current is not None:
        m = re.match(r"@@ -(\\d+)(?:,(\\d+))? \\+(\\d+)(?:,(\\d+))? @@", line)
        if not m:
            print("REFUSED: bad hunk header:", line); sys.exit(2)
        current["hunks"].append({"start": int(m.group(1)), "lines": []})
    elif current is not None and current["hunks"] and line[:1] in (" ", "+", "-", "\\\\"):
        current["hunks"][-1]["lines"].append(line)

changed = 0
for block in blocks:
    rel = (block["new"] or block["old"])
    rel = rel[2:] if rel[:2] in ("a/", "b/") else rel
    path = safe(rel)
    if not os.path.isfile(path):
        print("REFUSED: not a file:", rel); sys.exit(2)
    original = open(path, encoding="utf-8").read().splitlines()
    out, cursor = [], 0
    for hunk in block["hunks"]:
        start = hunk["start"] - 1
        if start < cursor or start > len(original):
            print("REFUSED: hunk out of range in", rel); sys.exit(2)
        out.extend(original[cursor:start])
        cursor = start
        for entry in hunk["lines"]:
            tag, text = entry[0], entry[1:]
            if tag == " ":
                if cursor >= len(original) or original[cursor] != text:
                    print("CONTEXT MISMATCH in", rel, "at line", cursor + 1); sys.exit(3)
                out.append(text); cursor += 1
            elif tag == "-":
                if cursor >= len(original) or original[cursor] != text:
                    print("CONTEXT MISMATCH in", rel, "at line", cursor + 1); sys.exit(3)
                cursor += 1
            elif tag == "+":
                out.append(text)
    out.extend(original[cursor:])
    open(path, "w", encoding="utf-8").write("\\n".join(out) + "\\n")
    changed += 1

print("APPLIED", changed, "file(s)")
'''


def validate_patch(
    parsed: ParsedPatch,
    *,
    workspace: Path,
    test_target: str = "",
    timeout_seconds: int = 180,
) -> ValidationResult:
    """Apply the patch to a copy and run the tests, entirely in the sandbox.

    The copy matters: the caller's workspace is never mutated, so a patch that
    half-applies cannot corrupt the snapshot the rest of the run is reading.
    """
    with tempfile.TemporaryDirectory(prefix="aisep-patch-") as temporary:
        scratch = Path(temporary) / "workspace"
        shutil.copytree(workspace, scratch, symlinks=False, dirs_exist_ok=False)

        (scratch / ".aisep_apply.py").write_text(_APPLIER, encoding="utf-8")
        (scratch / ".aisep_patch.diff").write_text(parsed.diff, encoding="utf-8")

        applied = sandbox_run(
            ["python", ".aisep_apply.py", "/workspace", ".aisep_patch.diff"],
            workspace=scratch,
            timeout_seconds=60,
        )
        if not applied.succeeded:
            return ValidationResult(
                applied=False,
                tests_passed=None,
                output=_join(applied),
                exit_code=applied.exit_code,
                timed_out=applied.timed_out,
            )

        argv = ["python", "-m", "pytest", "-q", "--no-header"]
        if test_target:
            _reject_unsafe_path(test_target)
            argv.append(test_target)

        tested = sandbox_run(argv, workspace=scratch, timeout_seconds=timeout_seconds)
        logger.info(
            "patch_validated",
            files=len(parsed.files),
            applied=True,
            tests_passed=tested.succeeded,
        )
        return ValidationResult(
            applied=True,
            tests_passed=tested.succeeded,
            output=f"{_join(applied)}\n\n--- tests ---\n{_join(tested)}",
            exit_code=tested.exit_code,
            timed_out=tested.timed_out,
        )


def _join(result: SandboxResult) -> str:
    parts = [result.stdout.strip(), result.stderr.strip()]
    return "\n".join(part for part in parts if part)[:8000]
