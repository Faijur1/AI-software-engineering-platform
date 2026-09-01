"""Benchmark results.

Reads the reports the evaluation harness writes; it never runs the benchmark
itself. Running it takes minutes, needs the embedding model, and is a
development activity rather than a user-facing one, so triggering it from an
HTTP request would be the wrong shape entirely.

A report is therefore always a record of an actual past run, with the commit
and chunk count it was measured against. If no run has happened, this endpoint
says so rather than returning zeros.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.schemas.evaluation import EvaluationReportResponse

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
logger = get_logger(__name__)

# eval/results/, written by `python -m eval`.
RESULTS_DIR = Path(__file__).resolve().parents[2] / "eval" / "results"


@router.get(
    "",
    response_model=EvaluationReportResponse,
    summary="The most recent benchmark run",
)
def latest_evaluation(user: CurrentUser) -> EvaluationReportResponse:
    """Return the most recent saved benchmark report.

    Not scoped to the caller: the benchmark measures this codebase's retrieval,
    not any user's data, and contains nothing user-specific. Authentication is
    still required so it is not an anonymous surface.
    """
    report = _load_latest()
    if report is None:
        raise NotFoundError(
            "No benchmark has been run yet. Run it with: python -m eval"
        )

    configurations = {
        name: {
            "recall": {str(k): v for k, v in config["recall"].items()},
            "precision": {str(k): v for k, v in config["precision"].items()},
            "hit_rate": {str(k): v for k, v in config["hit_rate"].items()},
            "mrr": config["mrr"],
            "by_style": config["by_style"],
            "elapsed_seconds": config["elapsed_seconds"],
        }
        for name, config in report["configurations"].items()
    }

    return EvaluationReportResponse(
        generated_at=report.get("generated_at"),
        repository=report["repository"],
        commit=report.get("commit"),
        chunk_count=report["chunk_count"],
        question_count=report["question_count"],
        cutoffs=report["cutoffs"],
        reranker=report["reranker"],
        configurations=configurations,
    )


def _load_latest() -> dict[str, Any] | None:
    """Read the newest report file, or None if there are none."""
    if not RESULTS_DIR.is_dir():
        return None
    reports = sorted(RESULTS_DIR.glob("*.json"))
    if not reports:
        return None

    try:
        loaded: dict[str, Any] = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A truncated report is worse than none: reporting partial numbers as
        # if they were a result is exactly what this project refuses to do.
        logger.warning("evaluation_report_unreadable", path=reports[-1].name)
        return None
    return loaded
