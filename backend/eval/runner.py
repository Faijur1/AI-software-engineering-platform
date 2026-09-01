"""Run the benchmark and report the numbers.

Three configurations are measured against the same questions: vector only,
keyword only, and hybrid. Reporting hybrid alone would be an assertion; the
comparison is the evidence.

The reranker is the passthrough throughout, deliberately. This is the baseline
the real cross-encoder in milestone 7 will be measured against, and it is only
a baseline if nothing has already reordered the results.

Every number here comes from an actual run against a real index. Nothing is
estimated, and a stale benchmark stops the run rather than scoring zero.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.base import EmbeddingProvider
from app.models.file import File
from app.rag.retriever import retrieve
from app.rag.types import RetrievalMethod
from eval.benchmark import BENCHMARK, Question, QuestionStyle, expected_paths
from eval.metrics import mean, score_query

# Cutoffs worth reporting. 5 is roughly what fits a context window alongside a
# prompt; 1 and 3 show whether the answer is at the top rather than merely
# present; 10 is the full retrieved set.
CUTOFFS: Final = (1, 3, 5, 10)

CONFIGURATIONS: Final = {
    "vector_only": {"use_vector": True, "use_keyword": False},
    "keyword_only": {"use_vector": False, "use_keyword": True},
    "hybrid": {"use_vector": True, "use_keyword": True},
}


class StaleBenchmarkError(RuntimeError):
    """A labelled file is not in the index, so scores would be meaningless."""


@dataclass
class QuestionResult:
    id: str
    query: str
    style: str
    expected: list[str]
    retrieved: list[str]
    methods: list[str]
    scores: dict[int, dict[str, float]] = field(default_factory=dict)


@dataclass
class ConfigurationReport:
    name: str
    recall: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    hit_rate: dict[int, float] = field(default_factory=dict)
    by_style: dict[str, dict[str, float]] = field(default_factory=dict)
    questions: list[QuestionResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass
class BenchmarkReport:
    repository: str
    commit: str | None
    chunk_count: int
    question_count: int
    cutoffs: list[int]
    reranker: str
    configurations: dict[str, ConfigurationReport] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def assert_labels_are_current(session: Session, repository_id: uuid.UUID) -> None:
    """Refuse to run if any labelled file is missing from the index.

    A benchmark whose labels have drifted scores zero on the questions it can
    no longer answer, which looks exactly like a retrieval regression. Failing
    loudly is the only way to keep the numbers trustworthy.
    """
    indexed = {
        row.path
        for row in session.execute(
            select(File).where(File.repository_id == repository_id)
        ).scalars()
    }
    missing = sorted(expected_paths() - indexed)
    if missing:
        raise StaleBenchmarkError(
            "These benchmark labels are not in the index, so the scores would "
            "be misleading. Re-index, or update the labels:\n  "
            + "\n  ".join(missing)
        )


def run_question(
    session: Session,
    embedder: EmbeddingProvider,
    *,
    repository_id: uuid.UUID,
    question: Question,
    use_vector: bool,
    use_keyword: bool,
) -> QuestionResult:
    """Retrieve for one question and score it at every cutoff."""
    result = retrieve(
        session,
        embedder,
        repository_id=repository_id,
        query=question.query,
        limit=max(CUTOFFS),
        use_vector=use_vector,
        use_keyword=use_keyword,
    )

    # Deduplicated to file granularity, preserving rank order: two chunks from
    # the same file are one piece of evidence, and counting them twice would
    # inflate precision for a retriever that returns several chunks per file.
    ordered_paths: list[str] = []
    methods: list[str] = []
    for chunk in result.chunks:
        if chunk.file_path not in ordered_paths:
            ordered_paths.append(chunk.file_path)
            methods.append(chunk.method.value)

    outcome = QuestionResult(
        id=question.id,
        query=question.query,
        style=question.style.value,
        expected=sorted(question.expected_files),
        retrieved=ordered_paths,
        methods=methods,
    )
    for k in CUTOFFS:
        scored = score_query(ordered_paths, set(question.expected_files), k)
        outcome.scores[k] = {
            "recall": scored.recall,
            "precision": scored.precision,
            "reciprocal_rank": scored.reciprocal_rank,
            "hit": float(scored.hit),
        }
    return outcome


def run_configuration(
    session: Session,
    embedder: EmbeddingProvider,
    *,
    repository_id: uuid.UUID,
    name: str,
    use_vector: bool,
    use_keyword: bool,
) -> ConfigurationReport:
    started = time.perf_counter()
    report = ConfigurationReport(name=name)

    for question in BENCHMARK:
        report.questions.append(
            run_question(
                session,
                embedder,
                repository_id=repository_id,
                question=question,
                use_vector=use_vector,
                use_keyword=use_keyword,
            )
        )

    for k in CUTOFFS:
        report.recall[k] = mean([q.scores[k]["recall"] for q in report.questions])
        report.precision[k] = mean([q.scores[k]["precision"] for q in report.questions])
        report.hit_rate[k] = mean([q.scores[k]["hit"] for q in report.questions])

    # MRR over the full retrieved list, which is the standard definition.
    report.mrr = mean(
        [q.scores[max(CUTOFFS)]["reciprocal_rank"] for q in report.questions]
    )

    # Broken down by phrasing, because the whole argument for hybrid search is
    # that the two retrievers fail on different question styles. An aggregate
    # average would hide exactly the effect being claimed.
    for style in QuestionStyle:
        subset = [q for q in report.questions if q.style == style.value]
        if not subset:
            continue
        report.by_style[style.value] = {
            "count": float(len(subset)),
            "recall@5": mean([q.scores[5]["recall"] for q in subset]),
            "hit@5": mean([q.scores[5]["hit"] for q in subset]),
            "mrr": mean([q.scores[max(CUTOFFS)]["reciprocal_rank"] for q in subset]),
        }

    report.elapsed_seconds = time.perf_counter() - started
    return report


def run_benchmark(
    session: Session,
    embedder: EmbeddingProvider,
    *,
    repository_id: uuid.UUID,
    repository_name: str,
    commit: str | None,
    chunk_count: int,
) -> BenchmarkReport:
    """Run every configuration over the whole benchmark."""
    assert_labels_are_current(session, repository_id)

    report = BenchmarkReport(
        repository=repository_name,
        commit=commit,
        chunk_count=chunk_count,
        question_count=len(BENCHMARK),
        cutoffs=list(CUTOFFS),
        reranker="passthrough",
    )

    for name, flags in CONFIGURATIONS.items():
        report.configurations[name] = run_configuration(
            session,
            embedder,
            repository_id=repository_id,
            name=name,
            use_vector=bool(flags["use_vector"]),
            use_keyword=bool(flags["use_keyword"]),
        )

    return report


def format_report(report: BenchmarkReport) -> str:
    """Render the report as a plain-text table."""
    lines: list[str] = []
    lines.append(f"Repository : {report.repository}")
    lines.append(f"Commit     : {(report.commit or '?')[:12]}")
    lines.append(f"Chunks     : {report.chunk_count}")
    lines.append(f"Questions  : {report.question_count}")
    lines.append(f"Reranker   : {report.reranker} (inert -- this is the baseline)")
    lines.append("")

    header = f"{'configuration':<14}" + "".join(f"{'R@' + str(k):>8}" for k in CUTOFFS)
    header += "".join(f"{'P@' + str(k):>8}" for k in CUTOFFS) + f"{'MRR':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for name, config in report.configurations.items():
        row = f"{name:<14}"
        row += "".join(f"{config.recall[k]:>8.3f}" for k in CUTOFFS)
        row += "".join(f"{config.precision[k]:>8.3f}" for k in CUTOFFS)
        row += f"{config.mrr:>8.3f}"
        lines.append(row)

    lines.append("")
    lines.append("By question style (recall@5 / hit@5):")
    styles = sorted({s for c in report.configurations.values() for s in c.by_style})
    lines.append(f"{'configuration':<14}" + "".join(f"{s:>22}" for s in styles))
    for name, config in report.configurations.items():
        row = f"{name:<14}"
        for style in styles:
            stats = config.by_style.get(style)
            row += (
                f"{stats['recall@5']:>11.3f} / {stats['hit@5']:<9.3f}"
                if stats
                else f"{'-':>22}"
            )
        lines.append(row)

    return "\n".join(lines)


def find_regressions(
    report: BenchmarkReport, *, configuration: str = "hybrid", cutoff: int = 5
) -> list[QuestionResult]:
    """Questions the given configuration failed entirely.

    Listed by name in the report so failures are specific and actionable rather
    than absorbed into an average.
    """
    config = report.configurations[configuration]
    return [q for q in config.questions if q.scores[cutoff]["hit"] == 0.0]


__all__ = [
    "BenchmarkReport",
    "ConfigurationReport",
    "QuestionResult",
    "RetrievalMethod",
    "StaleBenchmarkError",
    "assert_labels_are_current",
    "find_regressions",
    "format_report",
    "run_benchmark",
]
