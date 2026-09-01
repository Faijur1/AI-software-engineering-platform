"""Retrieval metrics.

Hand-computed expectations throughout. The point of this milestone is that the
reported numbers can be checked, so the checker itself has to be checkable.
"""

from __future__ import annotations

import pytest

from eval.benchmark import BENCHMARK, QuestionStyle, expected_paths
from eval.metrics import (
    mean,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_query,
)

RANKED = ["a.py", "b.py", "c.py", "d.py", "e.py"]


def test_recall_counts_relevant_files_found() -> None:
    # Two relevant, one of them in the top 3.
    assert recall_at_k(RANKED, {"c.py", "z.py"}, 3) == 0.5
    # Both found by k=5.
    assert recall_at_k(RANKED, {"c.py", "e.py"}, 5) == 1.0
    # Neither found in the top 1.
    assert recall_at_k(RANKED, {"c.py", "e.py"}, 1) == 0.0


def test_precision_divides_by_k_not_by_results_returned() -> None:
    """Returning three results where ten were allowed is genuinely worse.

    Normalising by the length of a short list would hide an under-filled
    context window rather than penalise it.
    """
    short = ["a.py", "b.py"]

    assert precision_at_k(short, {"a.py"}, 10) == pytest.approx(0.1)
    # Same one hit, but the budget was used: identical precision at k=2.
    assert precision_at_k(short, {"a.py"}, 2) == 0.5


def test_reciprocal_rank_rewards_position_not_just_presence() -> None:
    assert reciprocal_rank(RANKED, {"a.py"}) == 1.0
    assert reciprocal_rank(RANKED, {"b.py"}) == 0.5
    assert reciprocal_rank(RANKED, {"e.py"}) == pytest.approx(0.2)
    assert reciprocal_rank(RANKED, {"missing.py"}) == 0.0


def test_no_relevant_files_scores_zero_rather_than_dividing_by_zero() -> None:
    assert recall_at_k(RANKED, set(), 5) == 0.0
    assert precision_at_k(RANKED, set(), 5) == 0.0
    assert precision_at_k(RANKED, {"a.py"}, 0) == 0.0


def test_empty_results_score_zero() -> None:
    assert recall_at_k([], {"a.py"}, 5) == 0.0
    assert reciprocal_rank([], {"a.py"}) == 0.0


def test_score_query_reports_a_hit_only_within_the_cutoff() -> None:
    within = score_query(RANKED, {"c.py"}, 3)
    beyond = score_query(RANKED, {"e.py"}, 3)

    assert within.hit is True
    assert beyond.hit is False
    # A miss inside the cutoff must not carry a reciprocal rank from beyond it.
    assert beyond.reciprocal_rank == 0.0


def test_mean_of_nothing_is_zero_not_an_error() -> None:
    assert mean([]) == 0.0
    assert mean([1.0, 0.0]) == 0.5


# --- the benchmark itself -------------------------------------------------


def test_question_ids_are_unique() -> None:
    ids = [q.id for q in BENCHMARK]
    assert len(ids) == len(set(ids))


def test_every_question_has_labels_and_a_rationale() -> None:
    """A label without a stated reason cannot be argued with, only changed."""
    for question in BENCHMARK:
        assert question.expected_files, question.id
        assert question.rationale.strip(), question.id
        assert question.query.strip(), question.id


def test_the_benchmark_is_not_loaded_toward_one_retriever() -> None:
    """A benchmark of identifier questions would flatter keyword search.

    The mix is the control that keeps the comparison meaningful, so it is
    asserted rather than left to drift as questions are added.
    """
    counts = {style: 0 for style in QuestionStyle}
    for question in BENCHMARK:
        counts[question.style] += 1

    assert len(BENCHMARK) >= 20, "docs/rag.md specifies 20-30 questions"
    assert len(BENCHMARK) <= 30
    for style, count in counts.items():
        assert count >= 5, f"only {count} {style.value} questions"


def test_expected_paths_collects_every_label() -> None:
    paths = expected_paths()

    assert paths
    assert all(path.startswith(("backend/", "frontend/", "docs/")) for path in paths)
