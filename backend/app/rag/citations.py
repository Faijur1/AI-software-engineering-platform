"""Check the citations in a generated answer.

Deterministic checks only. Whether every *claim* is grounded in its cited
source is a judgement call that needs a model to make, and an unvalidated
LLM judge would produce a number nobody has any reason to trust -- which is
exactly the kind of fabricated metric this project refuses. What can be checked
mechanically is checked here; the rest is stated as not measured.

What this verifies:

- every ``[n]`` in the answer refers to a source that was actually retrieved
- how much of the answer carries any citation at all
- which retrieved sources the answer ended up using
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.context import Source

# Matches [1] and [1, 3] and [1][2]; deliberately not markdown links, because
# the prompt asks for bare bracketed numbers and anything else is a deviation
# worth seeing rather than silently accepting.
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# A sentence for the purposes of "did this claim cite anything". Crude, and
# only used for a ratio, never to alter the answer.
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(slots=True)
class CitationReport:
    """What the citations in one answer actually referred to."""

    cited_indices: list[int] = field(default_factory=list)
    # Indices the model cited that were never retrieved. Any value here means
    # the answer pointed at evidence that does not exist.
    invalid_indices: list[int] = field(default_factory=list)
    # Retrieved sources the answer never referred to.
    unused_indices: list[int] = field(default_factory=list)
    sentences: int = 0
    sentences_with_citation: int = 0

    @property
    def is_valid(self) -> bool:
        """Whether every citation points at a real retrieved source."""
        return not self.invalid_indices

    @property
    def citation_coverage(self) -> float:
        """Fraction of sentences carrying at least one citation.

        A coverage figure, not a groundedness figure: it says how much of the
        answer claims to rest on evidence, not whether the evidence supports it.
        """
        if self.sentences == 0:
            return 0.0
        return self.sentences_with_citation / self.sentences


def check_citations(answer: str, sources: list[Source]) -> CitationReport:
    """Verify the bracketed citations in ``answer`` against ``sources``."""
    valid = {source.index for source in sources}
    report = CitationReport()

    seen: list[int] = []
    for match in _CITATION.finditer(answer):
        for part in match.group(1).split(","):
            try:
                index = int(part.strip())
            except ValueError:  # pragma: no cover - regex guarantees digits
                continue
            if index not in seen:
                seen.append(index)

    report.cited_indices = [i for i in seen if i in valid]
    report.invalid_indices = [i for i in seen if i not in valid]
    report.unused_indices = sorted(valid - set(seen))

    for candidate in _SENTENCE.finditer(answer):
        sentence = candidate.group().strip()
        # Ignore fragments that are only whitespace or a lone citation marker.
        if len(sentence) < 15:
            continue
        report.sentences += 1
        if _CITATION.search(sentence):
            report.sentences_with_citation += 1

    return report
