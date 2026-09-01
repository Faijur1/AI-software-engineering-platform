"""The benchmark results endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.routes import evaluations


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
            json.dumps({"commit": commit}), encoding="utf-8"
        )
    monkeypatch.setattr(evaluations, "RESULTS_DIR", tmp_path)

    loaded = evaluations._load_latest()

    assert loaded is not None
    assert loaded["commit"] == "new"
