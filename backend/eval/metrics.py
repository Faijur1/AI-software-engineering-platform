"""Retrieval metrics.

Deliberately plain arithmetic with no library behind it, because the whole
value of this milestone is that the numbers can be checked by hand. Each
function takes the ranked list of retrieved file paths and the set of paths
that are actually relevant.

Relevance is judged at **file** granularity, not chunk. A question like "how
are secrets excluded from indexing" is answered by ``filters.py``; whether the
retriever returned the ``classify`` chunk or the ``is_secret_path`` chunk is a
distinction the benchmark has no principled basis to make, and pretending
otherwise would bake an arbitrary judgement into every score.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueryScores:
    """Metrics for one question at one cutoff."""

    k: int
    recall: float
    precision: float
    reciprocal_rank: float
    hit: bool


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant files that appear in the top ``k`` results.

    The question this answers is "did we find the answer at all", which is the
    one that matters most for RAG: a relevant chunk missing from the context
    cannot be cited, however good the model is.
    """
    if not relevant:
        return 0.0
    found = {path for path in retrieved[:k] if path in relevant}
    return len(found) / len(relevant)


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top ``k`` results that are relevant.

    Divided by ``k`` rather than by ``len(retrieved[:k])``. Returning three
    results of which two are relevant is genuinely worse at k=10 than returning
    ten with the same two, because the context budget was there to be used --
    normalising by the short list would hide that.
    """
    if k <= 0:
        return 0.0
    hits = sum(1 for path in retrieved[:k] if path in relevant)
    return hits / k


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """1 / rank of the first relevant result, or 0 if none appears.

    Sensitive to *where* the answer landed rather than only whether it did,
    which is what distinguishes "first" from "tenth" when both count as a hit.
    """
    for position, path in enumerate(retrieved, start=1):
        if path in relevant:
            return 1.0 / position
    return 0.0


def score_query(retrieved: list[str], relevant: set[str], k: int) -> QueryScores:
    return QueryScores(
        k=k,
        recall=recall_at_k(retrieved, relevant, k),
        precision=precision_at_k(retrieved, relevant, k),
        reciprocal_rank=reciprocal_rank(retrieved[:k], relevant),
        hit=any(path in relevant for path in retrieved[:k]),
    )


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
