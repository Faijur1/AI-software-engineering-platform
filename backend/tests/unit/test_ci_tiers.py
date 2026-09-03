"""CI's test tiers must cover every marker, and must not overlap.

Written after a real failure. The Kafka tests carried only the ``kafka`` marker,
and CI's unit selector was ``not integration and not sandbox and not llm`` — so
they were swept into the unit tier and ran against a broker that was not there.
Locally they passed, because a broker *was* there.

The failure mode is quiet in both directions: a marker missing from every
selector means those tests never run in CI at all, and a marker missing from an
exclusion means they run in the wrong tier. Neither shows up as an error until
something environmental differs, which is exactly when it is hardest to read.

So this parses the workflow and the marker declarations and checks them against
each other. It needs no broker, no database and no pytest subprocess.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT.parent / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"

# The tier that runs everything not claimed by a specialised tier. Every other
# marker has to be excluded from it, or those tests land here by default.
DEFAULT_TIER_PREFIX = "not integration"


def _declared_markers() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    return {entry.split(":", 1)[0].strip() for entry in markers}


def _selectors() -> list[str]:
    """Every ``-m "..."`` expression the workflow runs."""
    text = WORKFLOW.read_text(encoding="utf-8")
    return re.findall(r'-m\s+"([^"]+)"', text) + re.findall(r"-m\s+(\w+)\s", text)


@pytest.fixture(scope="module")
def selectors() -> list[str]:
    found = _selectors()
    assert found, "no pytest selectors found in the workflow; the regex is stale"
    return found


def test_every_declared_marker_is_run_by_some_tier(selectors: list[str]) -> None:
    """A marker no tier selects is a set of tests CI never runs.

    ``llm`` is the deliberate exception: it needs a live model, which CI has no
    business providing, and that gap is stated in docs/deployment.md rather than
    quietly tolerated.
    """
    joined = " ".join(selectors)
    never_run = {
        marker
        for marker in _declared_markers()
        if marker != "llm" and not re.search(rf"(?<!not )\b{marker}\b", joined)
    }

    assert not never_run, (
        f"these markers are declared but no CI tier selects them: {sorted(never_run)}. "
        "Tests CI never runs are tests CI does not cover."
    )


def test_the_default_tier_excludes_every_specialised_marker(
    selectors: list[str],
) -> None:
    """The bug this file exists for.

    The default tier is a negation, so anything not explicitly excluded from it
    falls in. A marker added without updating this selector runs its tests in a
    job that has none of what they need.
    """
    default = next((s for s in selectors if s.startswith(DEFAULT_TIER_PREFIX)), None)
    assert default is not None, "the default tier selector was not found"

    missing = {
        marker
        for marker in _declared_markers()
        if not re.search(rf"not {marker}\b", default)
    }

    assert not missing, (
        f"the default CI tier does not exclude {sorted(missing)}, so those tests "
        f"run in it. Selector was: {default!r}"
    )


def test_each_specialised_tier_names_exactly_one_marker(
    selectors: list[str],
) -> None:
    """A specialised tier should own one marker, and say so positively.

    The previous version of this test wandered through nested conditions and
    asserted almost nothing -- it passed on a selector that collected two
    tiers at once. Replaced with the property that actually matters and can be
    read in one line: each non-default tier positively names a single marker,
    so every test has exactly one job that owns it.
    """
    specialised = [s for s in selectors if not s.startswith(DEFAULT_TIER_PREFIX)]
    assert specialised, "no specialised tiers found; the workflow or regex changed"

    for selector in specialised:
        named = re.findall(r"(?<!not )\b(integration|sandbox|kafka|llm)\b", selector)
        assert len(named) == 1, (
            f"selector {selector!r} positively names {named}; a tier that claims "
            "more than one marker makes it ambiguous which job owns a failure"
        )
