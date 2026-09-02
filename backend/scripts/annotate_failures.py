"""Turn a pytest JUnit report into GitHub Actions annotations.

A failing CI step reports only "Process completed with exit code 1" unless
something says more. The detail is in the log, and reading a log needs an
authenticated token -- which is exactly what nobody has when they are looking at
a red build from a phone, or when a token has expired.

Annotations are part of the run's public metadata, so the failing test names
show on the summary and through the API without any of that. This prints one
``::error::`` per failure; GitHub turns each into an annotation.

Usage:
    python scripts/annotate_failures.py report.xml

Exits 0 always. This is reporting, not gating: the pytest step already failed,
and a formatting problem here must not mask which tests broke.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape(text: str) -> str:
    """Escape the characters that terminate a workflow command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: annotate_failures.py <junit.xml>", file=sys.stderr)
        return 0

    report = Path(argv[1])
    if not report.is_file():
        # No report means pytest died before writing one -- a collection error,
        # for instance. Say so rather than reporting nothing at all.
        print("::error::No JUnit report was written; pytest failed before running tests")
        return 0

    try:
        tree = ET.parse(report)
    except ET.ParseError as exc:
        print(f"::error::Could not parse the JUnit report: {_escape(str(exc))}")
        return 0

    count = 0
    for case in tree.iter("testcase"):
        for outcome in ("failure", "error"):
            node = case.find(outcome)
            if node is None:
                continue
            count += 1
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            # The last lines of a pytest failure carry the assertion; the top is
            # usually setup noise.
            detail = (node.get("message") or node.text or "").strip()
            tail = "\n".join(detail.splitlines()[-12:])
            print(f"::error title={_escape(name)}::{_escape(tail) or outcome}")

    if count:
        print(f"::notice::{count} test(s) failed; see the annotations above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
