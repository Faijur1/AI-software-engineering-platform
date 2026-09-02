"""Run the agent benchmark.

    python -m eval.agent_cli [--repeats N] [--workspace PATH]

Records the Stage 1 agent baseline that ADR-007 makes the entry condition for
Stage 2. Requires Postgres with an indexed repository and a reachable model.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.database import session_scope
from app.core.errors import ExternalServiceError
from app.llm.ollama import OllamaEmbedder
from app.llm.providers import get_chat_provider
from app.models.repository import Repository
from eval.agent_benchmark import AGENT_BENCHMARK, TaskShape
from eval.agent_runner import (
    StaleBenchmarkError,
    failures,
    format_baseline,
    rescore,
    run_baseline,
)

RESULTS_DIR = Path(__file__).parent / "results"


def _rescore(path: Path) -> int:
    """Re-read a saved report and print it under the current metrics."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 2

    baseline = rescore(payload)
    print(f"Rescored {path.name} under the current scoring rules\n")
    print(format_baseline(baseline))

    missed = failures(baseline)
    print(f"\nStill missing the expected file or symbol: {len(missed)} run(s)")
    for result in missed:
        print(f"  {result.id}  {result.task[:66]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval.agent_cli", description=__doc__)
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help=(
            "runs per task. At 1 the numbers carry no variance estimate, which "
            "is stated in the report rather than left for the reader to assume"
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "local checkout the read_file tool may read. Without it that tool "
            "is refused, which changes the tool-selection numbers"
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=6)
    parser.add_argument(
        "--shape",
        choices=[shape.value for shape in TaskShape],
        help=(
            "run only tasks of this shape. Exists because a metered provider "
            "may not have the quota for a full sweep: a partial run reported as "
            "a partial run is useful, a partial run reported as a baseline is "
            "not"
        ),
    )
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument(
        "--rescore",
        type=Path,
        help=(
            "recompute a saved report under the current scoring rules instead "
            "of running the agent, so a metric fix can be compared on the same "
            "answers rather than on a second run of a nondeterministic model"
        ),
    )
    args = parser.parse_args()

    if args.rescore is not None:
        return _rescore(args.rescore)

    provider = get_chat_provider()

    selected = list(AGENT_BENCHMARK)
    if args.shape:
        selected = [t for t in selected if t.shape.value == args.shape]
        if not selected:
            print(f"No tasks with shape {args.shape}.", file=sys.stderr)
            return 2

    # The agent's search tools embed their queries, so the embedding model has
    # to be reachable before anything is worth starting.
    try:
        OllamaEmbedder().check_available()
    except ExternalServiceError as exc:
        print(f"Cannot run: {exc.message}", file=sys.stderr)
        return 2

    if args.workspace is not None and not args.workspace.is_dir():
        print(f"Workspace {args.workspace} is not a directory.", file=sys.stderr)
        return 2

    with session_scope() as session:
        repository = session.execute(select(Repository)).scalars().first()
        if repository is None:
            print("No indexed repository found.", file=sys.stderr)
            return 2

        try:
            baseline = run_baseline(
                session,
                repository_id=repository.id,
                repository_name=repository.full_name,
                commit=repository.current_commit,
                model=provider.model_name,
                provider=provider.name,
                workspace=args.workspace,
                max_iterations=args.max_iterations,
                repeats=args.repeats,
                tasks=selected,
            )
        except StaleBenchmarkError as exc:
            print(f"Benchmark is stale:\n{exc}", file=sys.stderr)
            return 3

    print(format_baseline(baseline))

    missed = failures(baseline)
    print(f"\nNamed neither the expected file nor symbol: {len(missed)} run(s)")
    for result in missed:
        print(f"  {result.id} [{result.status}] {result.task[:64]}")
        print(f"      tools : {', '.join(result.tools_used) or '(none)'}")
        answer = (result.answer or "(no answer)").replace("\n", " ")
        print(f"      answer: {answer[:110]}")

    if args.repeats == 1:
        print(
            "\nNote: one run per task, so these numbers carry no variance "
            "estimate. Treat small differences as noise."
        )

    if not args.no_save:
        RESULTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = RESULTS_DIR / f"agent-{stamp}.json"
        payload = baseline.to_dict()
        # Marked so the API never mistakes an agent baseline for a retrieval
        # report; they share a directory.
        payload["kind"] = "agent"
        payload["generated_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved {path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
