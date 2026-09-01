"""Reciprocal rank fusion.

Pure functions over ranked lists, so these are exact assertions rather than
"looks about right" -- the merge is the part of hybrid search most likely to be
quietly wrong.
"""

from __future__ import annotations

import uuid

from app.rag.fusion import RRF_K, reciprocal_rank_fusion
from app.rag.types import Candidate, RetrievalMethod

IDS = [uuid.UUID(int=i) for i in range(10)]


def _candidate(index: int, rank: int, score: float = 0.5) -> Candidate:
    return Candidate(
        chunk_id=IDS[index],
        file_path=f"src/file{index}.py",
        symbol=f"symbol_{index}",
        kind="function",
        start_line=1,
        end_line=10,
        content=f"def symbol_{index}(): pass",
        score=score,
        rank=rank,
    )


def test_a_chunk_found_by_both_outranks_one_found_by_either() -> None:
    """The core property: agreement between independent retrievers wins.

    Chunk 1 is second in both lists; chunks 0 and 2 are first in one list only.
    Two contributions must beat one.
    """
    vector = [_candidate(0, 1), _candidate(1, 2)]
    keyword = [_candidate(2, 1), _candidate(1, 2)]

    fused = reciprocal_rank_fusion(vector, keyword)

    assert fused[0].chunk_id == IDS[1]
    assert fused[0].method is RetrievalMethod.both
    assert {c.method for c in fused[1:]} == {
        RetrievalMethod.vector,
        RetrievalMethod.keyword,
    }


def test_scores_are_the_published_rrf_formula() -> None:
    fused = reciprocal_rank_fusion([_candidate(0, 1)], [_candidate(0, 3)])

    expected = 1 / (RRF_K + 1) + 1 / (RRF_K + 3)
    assert fused[0].fused_score == expected


def test_incomparable_raw_scores_do_not_affect_the_merge() -> None:
    """The reason fusion is rank-based: ts_rank and cosine share no scale.

    The keyword result carries a raw score a thousand times larger. Only rank
    is used, so it must not dominate.
    """
    vector = [_candidate(0, 1, score=0.99), _candidate(1, 2, score=0.98)]
    keyword = [_candidate(1, 1, score=1500.0), _candidate(0, 2, score=0.0001)]

    fused = reciprocal_rank_fusion(vector, keyword)

    # Chunk 1 is (2, 1) and chunk 0 is (1, 2) -- symmetric, so they tie.
    assert fused[0].fused_score == fused[1].fused_score
    assert {c.chunk_id for c in fused} == {IDS[0], IDS[1]}


def test_deduplication_is_by_chunk_id() -> None:
    fused = reciprocal_rank_fusion([_candidate(0, 1)], [_candidate(0, 1)])

    assert len(fused) == 1
    assert fused[0].method is RetrievalMethod.both


def test_both_retrievers_evidence_is_preserved_for_the_inspector() -> None:
    """A fused score alone cannot answer "why was this ranked here"."""
    vector = [_candidate(0, 2, score=0.81)]
    keyword = [_candidate(0, 5, score=0.34)]

    hit = reciprocal_rank_fusion(vector, keyword)[0]

    assert hit.vector_score == 0.81
    assert hit.vector_rank == 2
    assert hit.keyword_score == 0.34
    assert hit.keyword_rank == 5


def test_rerank_score_starts_absent_not_zero() -> None:
    """Absent must stay visibly absent, never a fabricated number."""
    hit = reciprocal_rank_fusion([_candidate(0, 1)], [])[0]

    assert hit.rerank_score is None
    assert hit.selected is False


def test_either_list_may_be_empty() -> None:
    """A stopword-only query, or an unembedded repository, must still work."""
    only_vector = reciprocal_rank_fusion([_candidate(0, 1)], [])
    only_keyword = reciprocal_rank_fusion([], [_candidate(0, 1)])

    assert only_vector[0].method is RetrievalMethod.vector
    assert only_keyword[0].method is RetrievalMethod.keyword
    assert reciprocal_rank_fusion([], []) == []


def test_results_are_ordered_by_fused_score_descending() -> None:
    vector = [_candidate(i, i + 1) for i in range(5)]
    keyword = [_candidate(4, 1)]

    fused = reciprocal_rank_fusion(vector, keyword)

    scores = [c.fused_score for c in fused]
    assert scores == sorted(scores, reverse=True)
    # Chunk 4 was last by vector but first by keyword, so agreement lifts it.
    assert fused[0].chunk_id == IDS[4]


def test_ties_are_broken_deterministically() -> None:
    """Evaluation runs in milestone 6 must be reproducible."""
    vector = [_candidate(3, 1), _candidate(1, 1)]
    keyword: list[Candidate] = []

    first = reciprocal_rank_fusion(vector, keyword)
    second = reciprocal_rank_fusion(list(reversed(vector)), keyword)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
