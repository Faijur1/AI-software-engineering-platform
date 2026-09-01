"""Run the retrieval benchmark.

    python -m eval [--json path] [--repository owner/name]

Requires a running Postgres with an indexed, embedded repository, and a
reachable embedding model.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import session_scope
from app.core.errors import ExternalServiceError
from app.llm.ollama import OllamaEmbedder
from app.models.chunk import CodeChunk
from app.models.repository import Repository
from eval.runner import (
    StaleBenchmarkError,
    find_regressions,
    format_report,
    run_benchmark,
)

RESULTS_DIR = Path(__file__).parent / "results"


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval", description=__doc__)
    parser.add_argument("--repository", help="owner/name; defaults to the only one")
    parser.add_argument("--json", type=Path, help="write the full report here")
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="skip writing a timestamped report into eval/results/",
    )
    args = parser.parse_args()

    embedder = OllamaEmbedder()
    try:
        embedder.check_available()
    except ExternalServiceError as exc:
        print(f"Cannot run: {exc.message}", file=sys.stderr)
        return 2

    with session_scope() as session:
        query = select(Repository)
        if args.repository:
            owner, _, name = args.repository.partition("/")
            query = query.where(Repository.owner == owner, Repository.name == name)
        repository = session.execute(query).scalars().first()

        if repository is None:
            print("No indexed repository found. Index one first.", file=sys.stderr)
            return 2

        chunk_count = int(
            session.execute(
                select(func.count())
                .select_from(CodeChunk)
                .where(
                    CodeChunk.repository_id == repository.id,
                    CodeChunk.embedding.is_not(None),
                )
            ).scalar_one()
        )
        if chunk_count == 0:
            print(
                f"{repository.full_name} has no embedded chunks. Index it first.",
                file=sys.stderr,
            )
            return 2

        try:
            report = run_benchmark(
                session,
                embedder,
                repository_id=repository.id,
                repository_name=repository.full_name,
                commit=repository.current_commit,
                chunk_count=chunk_count,
            )
        except StaleBenchmarkError as exc:
            print(f"Benchmark is stale:\n{exc}", file=sys.stderr)
            return 3

    print(format_report(report))

    failures = find_regressions(report)
    print(f"\nHybrid found nothing relevant in the top 5 for {len(failures)} question(s):")
    for question in failures:
        print(f"  {question.id}  {question.query}")
        print(f"      expected : {', '.join(question.expected)}")
        print(f"      got      : {', '.join(question.retrieved[:3]) or '(nothing)'}")

    payload = report.to_dict()
    payload["generated_at"] = datetime.now(UTC).isoformat()

    if args.json:
        args.json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nWrote {args.json}")

    if not args.no_save:
        RESULTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = RESULTS_DIR / f"{stamp}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        cwd = Path.cwd()
        shown = path.relative_to(cwd) if path.is_relative_to(cwd) else path
        print(f"\nSaved {shown}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
