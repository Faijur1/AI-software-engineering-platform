"""Run the agent benchmark and compute the Stage 1 baseline.

The metrics ADR-007 names: task success rate, tool-selection accuracy,
iteration count, failure rate, execution latency.

One honesty constraint runs through all of it. **Success here is a mechanical
proxy**: it asks whether the answer named the file or symbol that answers the
task. It cannot tell whether the *explanation* is right, and milestone 7
already showed those come apart -- an answer named ``filters.py`` correctly
while attributing the mechanism to the wrong symbol. So the proxy is reported
as what it is, and a second, stricter reading is reported beside it: whether
the answer named the expected **symbol**, not merely the file it lives in.

Nothing here scores the explanation. Doing that needs a judge, and an
unvalidated judge would be a number with nothing behind it.
"""

from __future__ import annotations

import re
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.engine import run_agent
from app.agent.tools import Permission
from app.models.agent import AgentRun, AgentStatus, ToolRun, ToolStatus
from app.models.file import File
from eval.agent_benchmark import AGENT_BENCHMARK, AgentTask, TaskShape, expected_paths

DEFAULT_MAX_ITERATIONS: Final = 6


class StaleBenchmarkError(RuntimeError):
    """A labelled file is missing from the index, so scores would mislead."""


@dataclass
class TaskResult:
    id: str
    task: str
    shape: str
    status: str
    iterations: int
    duration_s: float
    answer: str | None
    # Mechanical proxy: the answer named an expected file or symbol.
    hit: bool
    # Stricter: it named the expected symbol, not just the file around it.
    named_symbol: bool
    tool_calls: int
    tools_succeeded: int
    tools_rejected: int
    tools_failed: int
    tools_used: list[str] = field(default_factory=list)
    # Whether every tool it chose was one that could plausibly answer this.
    tools_reasonable: bool = False


@dataclass
class AgentBaseline:
    # Model as actually used. Taken from the resolved provider rather than
    # from settings.llm_model, which names the *Ollama* model whatever provider
    # is configured -- a run against Gemini was saved labelled
    # "qwen2.5-coder:3b", which is worse than carrying no label at all.
    model: str
    provider: str
    repository: str
    commit: str | None
    task_count: int
    repeats: int
    max_iterations: int

    success_rate: float = 0.0
    symbol_rate: float = 0.0
    tool_validity: float = 0.0
    reasonable_tool_rate: float = 0.0
    mean_iterations: float = 0.0
    median_duration_s: float = 0.0
    status_counts: dict[str, int] = field(default_factory=dict)
    by_shape: dict[str, dict[str, float]] = field(default_factory=dict)
    results: list[TaskResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assert_labels_are_current(session: Session, repository_id: uuid.UUID) -> None:
    """Refuse to run if a labelled file is not in the index."""
    indexed = {
        row.path
        for row in session.execute(
            select(File).where(File.repository_id == repository_id)
        ).scalars()
    }
    missing = sorted(expected_paths() - indexed)
    if missing:
        raise StaleBenchmarkError(
            "These labels are not in the index, so the scores would mislead. "
            "Re-index, or update the labels:\n  " + "\n  ".join(missing)
        )


def _mentions(answer: str, needles: frozenset[str]) -> bool:
    """Whether the answer names any of ``needles``.

    Symbols match on word boundaries, so ``search`` is not satisfied by
    ``research``. Paths match either in full or by **basename**: an answer
    saying "in the `engine.py` file" has identified
    ``backend/app/agent/engine.py`` as surely as one quoting the whole path,
    and demanding the full path measured citation formatting rather than
    whether the answer was found.

    The basename rule is deliberately generous and would over-credit a generic
    name such as ``__init__.py``. No label uses one; if that changes, the
    labels are what to fix, because narrowing this rule again would bring back
    the formatting bias.
    """
    haystack = answer.replace("\\", "/").lower()
    for needle in needles:
        token = needle.replace("\\", "/").lower()
        if "/" in token or "." in token:
            if token in haystack:
                return True
            basename = token.rsplit("/", 1)[-1]
            if basename and basename in haystack:
                return True
        elif re.search(rf"\b{re.escape(token)}\b", haystack):
            return True
    return False


def run_task(
    session: Session,
    task: AgentTask,
    *,
    repository_id: uuid.UUID,
    workspace: Path | None,
    max_iterations: int,
    granted: frozenset[Permission],
) -> TaskResult:
    """Run one task and score it."""
    run = AgentRun(
        trace_id=uuid.uuid4().hex,
        repository_id=repository_id,
        task=task.task,
        max_iterations=max_iterations,
    )
    session.add(run)
    session.flush()

    started = time.perf_counter()
    outcome = run_agent(
        session,
        run,
        repository_id=repository_id,
        workspace=workspace,
        granted=granted,
    )
    duration = time.perf_counter() - started

    run.status = outcome.status
    run.result = outcome.answer
    run.iterations = outcome.iterations
    run.error = outcome.error
    session.flush()

    calls = list(
        session.execute(select(ToolRun).where(ToolRun.agent_run_id == run.id)).scalars()
    )
    answer = outcome.answer or ""
    used = [call.tool_name for call in calls]
    # Only real tool names count; "(unparsed)" is a parse failure, recorded
    # separately as a rejection rather than as a tool choice.
    chosen = [name for name in used if name != "(unparsed)"]

    return TaskResult(
        id=task.id,
        task=task.task,
        shape=task.shape.value,
        status=outcome.status.value,
        iterations=outcome.iterations,
        duration_s=round(duration, 1),
        answer=outcome.answer,
        hit=_mentions(answer, task.expected_files | task.expected_symbols),
        named_symbol=_mentions(answer, task.expected_symbols),
        tool_calls=len(calls),
        tools_succeeded=sum(1 for c in calls if c.status is ToolStatus.succeeded),
        tools_rejected=sum(1 for c in calls if c.status is ToolStatus.rejected),
        tools_failed=sum(1 for c in calls if c.status is ToolStatus.failed),
        tools_used=used,
        tools_reasonable=bool(chosen)
        and all(name in task.reasonable_tools for name in chosen),
    )


def run_baseline(
    session: Session,
    *,
    repository_id: uuid.UUID,
    repository_name: str,
    commit: str | None,
    model: str,
    provider: str = "ollama",
    workspace: Path | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    repeats: int = 1,
    granted: frozenset[Permission] = frozenset({Permission.repo_read}),
    tasks: list[AgentTask] | None = None,
) -> AgentBaseline:
    """Run every task ``repeats`` times and aggregate."""
    selected = tasks if tasks is not None else AGENT_BENCHMARK
    assert_labels_are_current(session, repository_id)

    baseline = AgentBaseline(
        model=model,
        provider=provider,
        repository=repository_name,
        commit=commit,
        task_count=len(selected),
        repeats=repeats,
        max_iterations=max_iterations,
    )

    for _ in range(repeats):
        for task in selected:
            baseline.results.append(
                run_task(
                    session,
                    task,
                    repository_id=repository_id,
                    workspace=workspace,
                    max_iterations=max_iterations,
                    granted=granted,
                )
            )

    _aggregate(baseline)
    return baseline


def _aggregate(baseline: AgentBaseline) -> None:
    results = baseline.results
    if not results:
        return

    total = len(results)
    baseline.success_rate = sum(r.hit for r in results) / total
    baseline.symbol_rate = sum(r.named_symbol for r in results) / total
    baseline.mean_iterations = sum(r.iterations for r in results) / total
    baseline.median_duration_s = statistics.median(r.duration_s for r in results)

    calls = sum(r.tool_calls for r in results)
    succeeded = sum(r.tools_succeeded for r in results)
    # Tool-selection accuracy, defined mechanically: a rejected call is a wrong
    # choice -- a tool that does not exist, or arguments that do not validate.
    baseline.tool_validity = succeeded / calls if calls else 0.0
    baseline.reasonable_tool_rate = sum(r.tools_reasonable for r in results) / total

    for result in results:
        baseline.status_counts[result.status] = (
            baseline.status_counts.get(result.status, 0) + 1
        )

    for shape in TaskShape:
        subset = [r for r in results if r.shape == shape.value]
        if not subset:
            continue
        baseline.by_shape[shape.value] = {
            "count": float(len(subset)),
            "success_rate": sum(r.hit for r in subset) / len(subset),
            "symbol_rate": sum(r.named_symbol for r in subset) / len(subset),
            "mean_iterations": sum(r.iterations for r in subset) / len(subset),
        }


def format_baseline(baseline: AgentBaseline) -> str:
    """Render the baseline as plain text."""
    lines: list[str] = []
    lines.append(f"Provider       : {baseline.provider or '?'}")
    lines.append(f"Model          : {baseline.model}")
    lines.append(f"Repository     : {baseline.repository}")
    lines.append(f"Commit         : {(baseline.commit or '?')[:12]}")
    lines.append(
        f"Tasks          : {baseline.task_count} x {baseline.repeats} repeat(s) "
        f"= {len(baseline.results)} runs"
    )
    lines.append(f"Iteration cap  : {baseline.max_iterations}")
    lines.append("")
    lines.append(f"task success (named file or symbol) : {baseline.success_rate:.3f}")
    lines.append(f"named the expected symbol           : {baseline.symbol_rate:.3f}")
    lines.append(f"tool validity (accepted / all calls): {baseline.tool_validity:.3f}")
    lines.append(f"chose only reasonable tools         : {baseline.reasonable_tool_rate:.3f}")
    lines.append(f"mean iterations                     : {baseline.mean_iterations:.2f}")
    lines.append(f"median run duration                 : {baseline.median_duration_s:.1f}s")
    lines.append("")
    lines.append("terminal status: " + ", ".join(
        f"{status}={count}" for status, count in sorted(baseline.status_counts.items())
    ))
    lines.append("")
    lines.append(f"{'shape':<16}{'n':>4}{'success':>10}{'symbol':>9}{'iters':>8}")
    lines.append("-" * 47)
    for shape, stats in sorted(baseline.by_shape.items()):
        lines.append(
            f"{shape:<16}{int(stats['count']):>4}{stats['success_rate']:>10.3f}"
            f"{stats['symbol_rate']:>9.3f}{stats['mean_iterations']:>8.2f}"
        )
    return "\n".join(lines)


def failures(baseline: AgentBaseline) -> list[TaskResult]:
    """Runs that named neither the expected file nor symbol."""
    return [result for result in baseline.results if not result.hit]


def rescore(payload: dict[str, Any]) -> AgentBaseline:
    """Recompute a saved report's metrics under the current scoring rules.

    A metric definition sometimes turns out to be wrong -- the first version of
    ``_mentions`` demanded a full path, so an answer naming ``engine.py`` was
    scored as a miss. Fixing that must not mean re-running 12 agent tasks, and
    more importantly the old and new numbers should be comparable on *the same
    answers* rather than on two different runs of a nondeterministic model.

    The stored answers are the evidence; this re-reads them.
    """
    lookup = {task.id: task for task in AGENT_BENCHMARK}

    baseline = AgentBaseline(
        model=str(payload["model"]),
        provider=str(payload.get("provider", "")),
        repository=str(payload["repository"]),
        commit=payload.get("commit"),
        task_count=int(payload["task_count"]),
        repeats=int(payload["repeats"]),
        max_iterations=int(payload["max_iterations"]),
    )

    for raw in payload["results"]:
        task = lookup.get(str(raw["id"]))
        if task is None:
            # A task removed from the benchmark since the report was written.
            # Skipped rather than guessed at, and the count reflects it.
            continue
        answer = raw.get("answer") or ""
        result = TaskResult(**raw)
        result.hit = _mentions(answer, task.expected_files | task.expected_symbols)
        result.named_symbol = _mentions(answer, task.expected_symbols)
        baseline.results.append(result)

    _aggregate(baseline)
    return baseline


__all__ = [
    "AgentBaseline",
    "AgentStatus",
    "StaleBenchmarkError",
    "TaskResult",
    "assert_labels_are_current",
    "failures",
    "format_baseline",
    "rescore",
    "run_baseline",
    "run_task",
]
