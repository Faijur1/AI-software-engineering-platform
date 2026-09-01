"""Context assembly, file-role classification, and citation checking."""

from __future__ import annotations

import uuid

import pytest

from app.rag.citations import check_citations, split_sentences
from app.rag.context import (
    CHARS_PER_TOKEN,
    MAX_CHUNK_TOKENS,
    build_context,
    estimate_tokens,
)
from app.rag.roles import FileRole, classify_role
from app.rag.types import RetrievalMethod, RetrievedChunk


def _chunk(index: int, content: str = "def f(): pass", path: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(int=index),
        file_path=path or f"src/file{index}.py",
        symbol=f"f{index}",
        kind="function",
        start_line=index * 10,
        end_line=index * 10 + 5,
        content=content,
        method=RetrievalMethod.both,
        fused_score=1.0 / (index + 1),
    )


# --- file roles -----------------------------------------------------------


def test_roles_are_classified_by_path() -> None:
    assert classify_role("backend/app/rag/vector.py") is FileRole.source
    assert classify_role("backend/tests/unit/test_x.py") is FileRole.test
    assert classify_role("docs/rag.md") is FileRole.docs
    assert classify_role("README.md") is FileRole.docs
    assert classify_role("backend/pyproject.toml") is FileRole.config
    assert classify_role(".env.example") is FileRole.config


def test_a_test_of_configuration_is_still_a_test() -> None:
    """Order matters: several rules can match one path."""
    assert classify_role("backend/tests/unit/test_config.py") is FileRole.test
    assert classify_role("frontend/src/app.test.tsx") is FileRole.test
    assert classify_role("backend/tests/conftest.py") is FileRole.test


def test_windows_separators_classify_the_same() -> None:
    assert classify_role("backend\\tests\\unit\\test_x.py") is FileRole.test


def test_a_directory_named_like_a_file_type_does_not_confuse_it() -> None:
    assert classify_role("backend/app/testing_utils.py") is FileRole.source
    assert classify_role("backend/app/documents.py") is FileRole.source


# --- context building -----------------------------------------------------


def test_sources_are_numbered_from_one_and_delimited() -> None:
    built = build_context([_chunk(1), _chunk(2)])

    assert [s.index for s in built.sources] == [1, 2]
    assert "<<<SOURCE 1" in built.prompt_context
    assert "SOURCE 1>>>" in built.prompt_context
    assert "<<<SOURCE 2" in built.prompt_context


def test_each_source_carries_a_checkable_location() -> None:
    """A citation that cannot be traced to real lines is decoration."""
    built = build_context([_chunk(3)])

    assert "src/file3.py:30-35" in built.prompt_context
    assert built.sources[0].file_path == "src/file3.py"
    assert built.sources[0].start_line == 30


def test_the_token_budget_is_enforced() -> None:
    big = "x" * int(500 * CHARS_PER_TOKEN)
    chunks = [_chunk(i, content=big) for i in range(1, 21)]

    built = build_context(chunks, token_budget=1500)

    assert built.included < built.offered
    assert built.estimated_tokens <= 1500
    assert built.dropped_for_budget > 0


def test_the_best_evidence_is_what_survives_the_budget() -> None:
    """Chunks are added in rank order, so truncation drops the worst."""
    chunks = [_chunk(i, content="y" * 2000) for i in range(1, 10)]

    built = build_context(chunks, token_budget=1200)

    assert built.sources[0].file_path == "src/file1.py"


def test_an_oversized_chunk_is_truncated_rather_than_dropped() -> None:
    """Half a large function is still evidence."""
    enormous = "z" * int(MAX_CHUNK_TOKENS * CHARS_PER_TOKEN * 3)

    built = build_context([_chunk(1, content=enormous)], token_budget=100_000)

    assert built.included == 1
    assert "(truncated)" in built.sources[0].content
    assert len(built.sources[0].content) < len(enormous)


def test_counts_are_reported_so_a_half_filled_context_is_visible() -> None:
    built = build_context([_chunk(i) for i in range(1, 4)])

    assert built.offered == 3
    assert built.included == 3
    assert built.estimated_tokens > 0


def test_no_chunks_produces_an_empty_context_not_an_error() -> None:
    built = build_context([])

    assert built.sources == []
    assert built.prompt_context == ""
    assert built.included == 0


def test_token_estimate_is_monotonic() -> None:
    assert estimate_tokens("") < estimate_tokens("hello")
    assert estimate_tokens("hello") < estimate_tokens("hello world " * 50)


# --- citations ------------------------------------------------------------


def test_valid_citations_are_recognised() -> None:
    sources = build_context([_chunk(1), _chunk(2)]).sources

    report = check_citations("The token is encrypted [1] before storage [2].", sources)

    assert report.is_valid
    assert report.cited_indices == [1, 2]
    assert report.invalid_indices == []


def test_a_citation_to_a_source_that_does_not_exist_is_caught() -> None:
    """The model inventing evidence is the failure that matters most."""
    sources = build_context([_chunk(1)]).sources

    report = check_citations("This is handled in the worker [4].", sources)

    assert not report.is_valid
    assert report.invalid_indices == [4]


def test_grouped_and_repeated_citations_are_parsed() -> None:
    sources = build_context([_chunk(i) for i in range(1, 4)]).sources

    report = check_citations("Both places do it [1, 3] and again [1].", sources)

    assert report.cited_indices == [1, 3]


def test_unused_sources_are_reported() -> None:
    sources = build_context([_chunk(1), _chunk(2), _chunk(3)]).sources

    report = check_citations("Only the first matters [1].", sources)

    assert report.unused_indices == [2, 3]


def test_citation_coverage_measures_claims_not_correctness() -> None:
    sources = build_context([_chunk(1)]).sources

    answer = (
        "The token is encrypted before it is stored [1]. "
        "It is probably also rotated somewhere on a schedule."
    )
    report = check_citations(answer, sources)

    assert report.sentences == 2
    assert report.sentences_with_citation == 1
    assert report.citation_coverage == 0.5


def test_an_answer_with_no_citations_scores_zero_coverage() -> None:
    sources = build_context([_chunk(1)]).sources

    report = check_citations("It works by magic and general good intentions.", sources)

    assert report.citation_coverage == 0.0
    assert report.cited_indices == []
    # No citations is not the same as invalid ones.
    assert report.is_valid


def test_a_period_in_a_filename_does_not_end_a_sentence() -> None:
    """Regression: this silently zeroed the coverage metric.

    An answer about code is full of periods that are not sentence ends --
    ``security.py``, ``obj.method()``. The naive splitter cut
    "...in `core/security.py` [3]." into a fragment holding only "py` [3].",
    discarded it as too short, and reported an answer that did cite as one that
    did not.
    """
    answer = (
        "Tokens are encrypted with the `TOKEN_ENCRYPTION_KEY` secret. "
        "This happens in `backend/app/core/security.py` [3]. "
        "The function raises if decryption fails."
    )
    sources = build_context([_chunk(i) for i in range(1, 4)]).sources

    report = check_citations(answer, sources)

    assert report.sentences == 3
    assert report.sentences_with_citation == 1
    assert report.citation_coverage == pytest.approx(1 / 3)
    assert report.cited_indices == [3]


def test_sentence_splitting_survives_code_spans() -> None:
    sentences = split_sentences(
        "Call `a.b.c()` first. Then read `x.py` and `y.json`. That is all."
    )

    assert len(sentences) == 3
    assert sentences[0].startswith("Call")
    assert sentences[1].startswith("Then read")


def test_a_one_word_fragment_is_not_counted_as_a_claim() -> None:
    """"Done." is not an uncited assertion about the code."""
    assert split_sentences("The worker retries [1]. Done.") == [
        "The worker retries [1]."
    ]


def test_bare_citation_fragments_are_not_counted_as_sentences() -> None:
    """A lone marker is not a claim, and would dilute the ratio."""
    sources = build_context([_chunk(1)]).sources

    report = check_citations("The worker retries on failure [1]. [1]", sources)

    assert report.sentences == 1
    assert report.citation_coverage == 1.0


def test_a_short_real_sentence_still_counts() -> None:
    sources = build_context([_chunk(1)]).sources

    report = check_citations("It is cached [1]. Nothing else.", sources)

    assert report.sentences == 2
    assert report.sentences_with_citation == 1
