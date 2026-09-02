"""The benchmark results endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.routes import evaluations


def _retrieval(**extra: object) -> str:
    """A minimal report of the shape the endpoint requires."""
    payload: dict[str, object] = {
        "configurations": {"hybrid": {"recall": {"5": 0.8}, "precision": {"5": 0.4}}}
    }
    payload.update(extra)
    return json.dumps(payload)


def test_reading_results_requires_a_session(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/evaluations")

    assert response.status_code == 401


def test_no_run_yet_says_so_rather_than_returning_zeros(
    anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Zeros would read as a catastrophic result rather than as no data."""
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    assert evaluations._load_latest() is None


def test_a_truncated_report_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Partial numbers presented as a result is exactly what is not allowed."""
    (tmp_path / "20260101T000000Z.json").write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    assert evaluations._load_latest() is None


def test_the_newest_report_is_the_one_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for stamp, commit in (("20260101T000000Z", "old"), ("20260202T000000Z", "new")):
        (tmp_path / f"{stamp}.json").write_text(
            _retrieval(commit=commit), encoding="utf-8"
        )
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    loaded = evaluations._load_latest()

    assert loaded is not None
    assert loaded["commit"] == "new"


# --- two kinds of report share one directory --------------------------------


def test_an_agent_baseline_is_not_served_as_a_retrieval_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: this endpoint answered 500.

    The agent benchmark writes into the same directory, and "agent-..." sorts
    after a bare timestamp, so the newest file was an agent baseline. Reading
    retrieval configurations out of it raised, and the caller got an internal
    error rather than the retrieval report that was sitting right there.
    """
    (tmp_path / "20260101T000000Z.json").write_text(
        _retrieval(commit="retrieval"), encoding="utf-8"
    )
    (tmp_path / "agent-20260202T000000Z.json").write_text(
        json.dumps({"kind": "agent", "success_rate": 0.583}), encoding="utf-8"
    )
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    loaded = evaluations._load_latest()

    assert loaded is not None
    assert loaded["commit"] == "retrieval"


def test_an_agent_baseline_alone_reads_as_no_retrieval_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Better the honest "nothing has been run" than someone else's numbers."""
    (tmp_path / "agent-20260202T000000Z.json").write_text(
        json.dumps({"kind": "agent", "success_rate": 0.583}), encoding="utf-8"
    )
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    assert evaluations._load_latest() is None


def test_older_reports_predate_the_kind_marker_and_still_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Selection is by shape, so reports written before the marker existed are
    not orphaned by it."""
    (tmp_path / "20260101T000000Z.json").write_text(
        _retrieval(commit="unmarked"), encoding="utf-8"
    )
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    loaded = evaluations._load_latest()

    assert loaded is not None
    assert loaded["commit"] == "unmarked"


def test_one_unreadable_report_does_not_hide_an_earlier_valid_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scanning stops at the newest *usable* report, not the newest file."""
    (tmp_path / "20260101T000000Z.json").write_text(
        _retrieval(commit="good"), encoding="utf-8"
    )
    (tmp_path / "20260202T000000Z.json").write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    loaded = evaluations._load_latest()

    assert loaded is not None
    assert loaded["commit"] == "good"
