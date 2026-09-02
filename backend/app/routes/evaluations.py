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
        question_set=report.get("question_set"),
        cutoffs=report["cutoffs"],
        reranker=report["reranker"],
        configurations=configurations,
    )


def _load_latest() -> dict[str, Any] | None:
    """Read the newest *retrieval* report, or None if there is none.

    The directory holds more than one kind of report: the agent benchmark
    writes here too. Selecting purely by filename order returned an agent
    baseline to a caller expecting retrieval configurations, and the endpoint
    answered 500. So each candidate is checked for the shape it must have, and
    anything else is skipped rather than coerced.

    Newest first, and the first readable match wins -- one unreadable or
    unexpected file must not hide every earlier valid one.
    """
    if not RESULTS_DIR.is_dir():
        return None

    for path in sorted(RESULTS_DIR.glob("*.json"), reverse=True):
        try:
            loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A truncated report is worse than none: reporting partial numbers
            # as if they were a result is what this project refuses to do.
            logger.warning("evaluation_report_unreadable", path=path.name)
            continue

        if loaded.get("kind") == "agent":
            logger.debug("evaluation_report_skipped", path=path.name)
            continue
        selected = _select_report(loaded)
        if selected is not None:
            # generated_at lives on the envelope, not the individual report.
            selected.setdefault("generated_at", loaded.get("generated_at"))
            return selected
        logger.debug("evaluation_report_skipped", path=path.name)

    return None


def _select_report(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the single report to serve from a saved file.

    The CLI writes ``{"kind": ..., "reports": [...]}`` because ``--set both``
    measures the tuning set and the held-out set in one run. Older files are a
    single report at the top level. Both shapes are read, because a reader that
    understood only one of them silently skipped every new run and kept serving
    a stale artifact -- which is exactly what happened: the endpoint reported
    three configurations for weeks after a fourth was added.

    When a file holds several, the **held-out** report is served. It is the
    honest measure: it was written before any tuning and used for confirmation
    only, so it is the number that generalises. Serving the tuning set without
    saying so would flatter the system.
    """
    reports = payload.get("reports")
    if isinstance(reports, list) and reports:
        valid = [r for r in reports if isinstance(r, dict) and _has_configurations(r)]
        if not valid:
            return None
        for report in valid:
            if report.get("question_set") == "heldout":
                return report
        return valid[-1]

    return payload if _has_configurations(payload) else None


def _has_configurations(payload: dict[str, Any]) -> bool:
    """Whether ``payload`` is a retrieval benchmark report.

    Checked by shape rather than by filename. Reports written before the agent
    benchmark existed carry no kind marker, so a name-based rule would have to
    special-case them; the fields a response actually needs are the honest
    test.
    """
    configurations = payload.get("configurations")
    if not isinstance(configurations, dict) or not configurations:
        return False
    return all(
        isinstance(entry, dict) and "recall" in entry and "precision" in entry
        for entry in configurations.values()
    )
