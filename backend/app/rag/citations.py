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
# Inline code spans are masked before sentence splitting. Without that, a
# period inside `security.py` or `obj.method()` looks like a sentence end --
# which, in an answer *about code*, is not an edge case but the common case.
# The naive splitter turned "...in `core/security.py` [3]." into a fragment
# containing only "py` [3].", which was then discarded as too short, so a real
# citation was scored as no citation at all.
_CODE_SPAN = re.compile(r"`[^`\n]*`")
# A sentence ends at .!? followed by whitespace or end of text.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
# A fragment counts as a sentence only if it holds at least two word-like
# tokens. That keeps stray punctuation and bare citation markers out of the
# denominator without discarding genuinely short sentences.
_WORD = re.compile(r"[A-Za-z]{2,}")


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

    for sentence in split_sentences(answer):
        report.sentences += 1
        if _CITATION.search(sentence):
            report.sentences_with_citation += 1

    return report


def split_sentences(text: str) -> list[str]:
    """Split an answer into sentences, tolerating code and file paths.

    Public so the behaviour can be tested directly: the failure it exists to
    prevent -- a citation lost to a period inside a filename -- is invisible in
    the aggregate ratio it feeds.
    """
    # Replaced with spaces of the same length, so every offset found in the
    # masked copy still indexes the original text.
    masked = _CODE_SPAN.sub(lambda match: " " * len(match.group()), text)

    sentences: list[str] = []
    start = 0
    for end in _SENTENCE_END.finditer(masked):
        sentences.append(text[start : end.end()])
        start = end.end()
    if start < len(text):
        sentences.append(text[start:])

    return [
        stripped
        for stripped in (sentence.strip() for sentence in sentences)
        if len(_WORD.findall(stripped)) >= 2
    ]
