"""Patch parsing, and validation inside the sandbox.

The parsing tests need nothing; the validation tests run real containers and
carry the ``sandbox`` marker.

What is being defended against here is a model producing a diff that writes
somewhere it should not. The applier re-checks every path itself, because it
runs where the write actually happens and must not depend on a check made
elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.patches import PatchRejected, parse_patch, validate_patch

GOOD_DIFF = """\
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,3 +1,3 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


# --- parsing ----------------------------------------------------------------


def test_a_well_formed_diff_parses() -> None:
    parsed = parse_patch(GOOD_DIFF)

    assert parsed.files == ["src/calc.py"]
    assert parsed.hunks == 1


@pytest.mark.parametrize(
    ("diff", "reason"),
    [
        ("", "empty"),
        ("   \n", "empty"),
        ("just some prose about the fix", "hunks"),
        ("--- a/x.py\n+++ b/x.py\n", "hunks"),
        ("@@ -1,1 +1,1 @@\n-a\n+b\n", "file headers"),
    ],
)
def test_an_unusable_diff_is_rejected_not_repaired(diff: str, reason: str) -> None:
    """Silently fixing malformed output would mean applying something nobody wrote."""
    with pytest.raises(PatchRejected, match=reason):
        parse_patch(diff)


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../../../etc/shadow",
        "src/../../escape.py",
        "C:/Windows/system32/config",
    ],
)
def test_a_diff_targeting_a_path_outside_the_repository_is_refused(path: str) -> None:
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n-a\n+b\n"

    with pytest.raises(PatchRejected):
        parse_patch(diff)


def test_a_diff_naming_an_unknown_file_is_refused_when_the_tree_is_known() -> None:
    with pytest.raises(PatchRejected, match="not a file in this repository"):
        parse_patch(GOOD_DIFF, workspace_files={"src/other.py"})


def test_an_oversized_diff_is_refused() -> None:
    huge = GOOD_DIFF + ("+" + "x" * 200 + "\n") * 2000

    with pytest.raises(PatchRejected, match="over the"):
        parse_patch(huge)


def test_a_new_file_diff_ignores_the_dev_null_side() -> None:
    diff = (
        "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,1 @@\n+print('hi')\n"
    )

    parsed = parse_patch(diff)

    assert parsed.files == ["src/new.py"]


# --- validation in the sandbox ----------------------------------------------

pytestmark_sandbox = pytest.mark.sandbox


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tiny project with a failing test that the patch under test fixes."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "test_calc.py").write_text(
        "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.sandbox
def test_a_correct_patch_applies_and_the_tests_pass(project: Path) -> None:
    result = validate_patch(parse_patch(GOOD_DIFF), workspace=project)

    assert result.applied
    assert result.tests_passed is True
    assert result.validated


@pytest.mark.sandbox
def test_the_original_workspace_is_never_mutated(project: Path) -> None:
    """A half-applied patch must not corrupt the snapshot the run is reading."""
    before = (project / "src" / "calc.py").read_text(encoding="utf-8")

    validate_patch(parse_patch(GOOD_DIFF), workspace=project)

    assert (project / "src" / "calc.py").read_text(encoding="utf-8") == before


@pytest.mark.sandbox
def test_a_patch_that_applies_but_breaks_the_tests_is_not_validated(
    project: Path,
) -> None:
    """Applied and validated are different facts, and conflating them is the
    failure this gate exists to prevent."""
    breaking = (
        "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,3 +1,3 @@\n"
        " def add(a, b):\n-    return a - b\n+    return a * b\n"
    )

    result = validate_patch(parse_patch(breaking), workspace=project)

    assert result.applied is True
    assert result.tests_passed is False
    assert result.validated is False


@pytest.mark.sandbox
def test_a_patch_whose_context_does_not_match_fails_to_apply(project: Path) -> None:
    stale = (
        "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,3 +1,3 @@\n"
        " def subtract(a, b):\n-    return a - b\n+    return a + b\n"
    )

    result = validate_patch(parse_patch(stale), workspace=project)

    assert result.applied is False
    assert result.tests_passed is None
    assert "CONTEXT MISMATCH" in result.output


@pytest.mark.sandbox
def test_the_applier_refuses_an_escaping_path_even_if_parsing_missed_it(
    project: Path,
) -> None:
    """Defence in depth: the applier runs where the write happens.

    The parser is bypassed here deliberately, to prove the container-side check
    is real rather than decorative.
    """
    from app.agent.patches import ParsedPatch

    escaping = ParsedPatch(
        diff=(
            "--- a/../../escape.py\n+++ b/../../escape.py\n"
            "@@ -1,1 +1,1 @@\n-a\n+b\n"
        ),
        files=["../../escape.py"],
        hunks=1,
    )

    result = validate_patch(escaping, workspace=project)

    assert result.applied is False
    assert "REFUSED" in result.output
