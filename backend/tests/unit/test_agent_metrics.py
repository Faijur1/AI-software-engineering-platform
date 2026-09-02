"""The agent benchmark's scoring.

The metrics ADR-007 requires are only useful if what they measure is stated
exactly. These tests pin the definitions, especially the ones that could
flatter the system if left vague.
"""

from __future__ import annotations

from eval.agent_benchmark import AGENT_BENCHMARK, TaskShape, expected_paths
from eval.agent_runner import AgentBaseline, TaskResult, _aggregate, _mentions


def _result(**kwargs: object) -> TaskResult:
    base: dict[str, object] = {
        "id": "t",
        "task": "t",
        "shape": TaskShape.lookup.value,
        "status": "succeeded",
        "iterations": 1,
        "duration_s": 1.0,
        "answer": "",
        "hit": False,
        "named_symbol": False,
        "tool_calls": 0,
        "tools_succeeded": 0,
        "tools_rejected": 0,
        "tools_failed": 0,
    }
    base.update(kwargs)
    return TaskResult(**base)  # type: ignore[arg-type]


# --- what counts as naming the answer ---------------------------------------


def test_a_symbol_is_matched_on_word_boundaries() -> None:
    """"search" must not be satisfied by "research"."""
    assert _mentions("it calls search() first", frozenset({"search"}))
    assert not _mentions("this needs more research", frozenset({"search"}))


def test_a_path_is_matched_as_a_substring() -> None:
    """Paths are distinctive enough that a substring match is safe."""
    assert _mentions(
        "see backend/app/rag/fusion.py line 40",
        frozenset({"backend/app/rag/fusion.py"}),
    )


def test_backslash_paths_still_match() -> None:
    # Raw string: a model on Windows may write native separators, and the
    # escapes here would otherwise become control characters rather than
    # backslashes.
    assert _mentions(
        r"see backend\app\rag\fusion.py", frozenset({"backend/app/rag/fusion.py"})
    )


def test_matching_is_case_insensitive() -> None:
    assert _mentions("The Chunk_Source function", frozenset({"chunk_source"}))


def test_an_empty_answer_never_counts_as_a_hit() -> None:
    assert not _mentions("", frozenset({"chunk_source"}))


# --- aggregation ------------------------------------------------------------


def test_tool_validity_counts_rejections_as_wrong_choices() -> None:
    """A rejected call is a tool that does not exist, or arguments that do not
    validate. Both are selection errors."""
    baseline = AgentBaseline(
        model="m", repository="r", commit=None, task_count=1, repeats=1, max_iterations=6
    )
    baseline.results = [
        _result(tool_calls=4, tools_succeeded=3, tools_rejected=1),
    ]

    _aggregate(baseline)

    assert baseline.tool_validity == 0.75


def test_no_tool_calls_scores_zero_validity_rather_than_dividing_by_zero() -> None:
    baseline = AgentBaseline(
        model="m", repository="r", commit=None, task_count=1, repeats=1, max_iterations=6
    )
    baseline.results = [_result()]

    _aggregate(baseline)

    assert baseline.tool_validity == 0.0


def test_success_and_symbol_rates_are_reported_separately() -> None:
    """Naming the file is a weaker claim than naming the symbol, and milestone
    7 showed the two come apart."""
    baseline = AgentBaseline(
        model="m", repository="r", commit=None, task_count=2, repeats=1, max_iterations=6
    )
    baseline.results = [
        _result(hit=True, named_symbol=True),
        _result(hit=True, named_symbol=False),
    ]

    _aggregate(baseline)

    assert baseline.success_rate == 1.0
    assert baseline.symbol_rate == 0.5


def test_results_are_broken_down_by_task_shape() -> None:
    """Iteration count means different things for a lookup and an investigation."""
    baseline = AgentBaseline(
        model="m", repository="r", commit=None, task_count=2, repeats=1, max_iterations=6
    )
    baseline.results = [
        _result(shape="lookup", iterations=1, hit=True),
        _result(shape="investigation", iterations=5, hit=False),
    ]

    _aggregate(baseline)

    assert baseline.by_shape["lookup"]["mean_iterations"] == 1.0
    assert baseline.by_shape["investigation"]["success_rate"] == 0.0


def test_terminal_statuses_are_counted_separately() -> None:
    """max_iterations_exceeded is not a failure and must not be merged into one."""
    baseline = AgentBaseline(
        model="m", repository="r", commit=None, task_count=3, repeats=1, max_iterations=6
    )
    baseline.results = [
        _result(status="succeeded"),
        _result(status="max_iterations_exceeded"),
        _result(status="failed"),
    ]

    _aggregate(baseline)

    assert baseline.status_counts == {
        "succeeded": 1,
        "max_iterations_exceeded": 1,
        "failed": 1,
    }


# --- the task set -----------------------------------------------------------


def test_task_ids_are_unique() -> None:
    ids = [task.id for task in AGENT_BENCHMARK]
    assert len(ids) == len(set(ids))


def test_every_task_has_labels_a_shape_and_a_rationale() -> None:
    for task in AGENT_BENCHMARK:
        assert task.expected_files, task.id
        assert task.expected_symbols, task.id
        assert task.reasonable_tools, task.id
        assert task.rationale.strip(), task.id


def test_both_task_shapes_are_represented() -> None:
    """Iteration count is only interpretable against the shape of the task."""
    shapes = {task.shape for task in AGENT_BENCHMARK}
    assert shapes == set(TaskShape)
    for shape in TaskShape:
        assert sum(1 for t in AGENT_BENCHMARK if t.shape is shape) >= 4


def test_expected_paths_are_repository_relative() -> None:
    assert all(path.startswith(("backend/", "frontend/", "docs/")) for path in expected_paths())
