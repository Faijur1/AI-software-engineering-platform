"""The agent loop, driven by a scripted model.

The model is injected rather than called, because the loop's behaviour under a
*bad* model is the part most worth testing and that is hard to provoke on
demand from a real one. The local model is weak, so every failure scripted here
is one it actually produces: invalid JSON, prose around the object, a
hallucinated tool name, a path traversal, never finishing.

Needs Postgres running and migrated.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.agent.engine import parse_action, run_agent
from app.agent.tools import Permission
from app.core.database import session_scope
from app.models.agent import AgentRun, AgentStatus, Event, ToolRun, ToolStatus
from app.models.chunk import ChunkKind, CodeChunk
from app.models.file import File
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.integration

GITHUB_ID = 900_000_111


class ScriptedModel:
    """Replays fixed replies, then repeats the last one."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._replies) - 1)
        return self._replies[index]


@pytest.fixture
def repository() -> Iterator[uuid.UUID]:
    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))

    with session_scope() as session:
        user = User(github_id=GITHUB_ID, login="agent-tester")
        session.add(user)
        session.flush()
        repo = Repository(
            user_id=user.id, github_id=GITHUB_ID + 1, owner="t", name="agentcorpus"
        )
        session.add(repo)
        session.flush()
        source = File(
            repository_id=repo.id,
            path="src/auth.py",
            language="python",
            content_hash="a" * 64,
            commit_sha="c" * 40,
            size_bytes=50,
        )
        session.add(source)
        session.flush()
        session.add(
            CodeChunk(
                file_id=source.id,
                repository_id=repo.id,
                content="def verify_state(state):\n    return state == expected\n",
                symbol="verify_state",
                kind=ChunkKind.function,
                start_line=1,
                end_line=2,
                chunk_hash="b" * 64,
            )
        )
        repo_id = repo.id

    yield repo_id

    with session_scope() as session:
        session.execute(delete(User).where(User.github_id == GITHUB_ID))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def verify_state(state):\n    return state == expected\n", encoding="utf-8"
    )
    return tmp_path


def _make_run(repository_id: uuid.UUID, *, max_iterations: int = 5) -> uuid.UUID:
    with session_scope() as session:
        run = AgentRun(
            trace_id=uuid.uuid4().hex,
            repository_id=repository_id,
            task="Where is the OAuth state verified?",
            max_iterations=max_iterations,
        )
        session.add(run)
        session.flush()
        return run.id


# --- parsing what a weak model actually emits -------------------------------


def test_a_clean_tool_call_parses() -> None:
    action = parse_action('{"thought": "look", "tool": "search_code", '
                          '"arguments": {"query": "state"}}')

    assert action.tool == "search_code"
    assert action.arguments == {"query": "state"}
    assert not action.is_final


def test_json_wrapped_in_prose_still_parses() -> None:
    """Small models narrate. That is not a reason to fail a run."""
    action = parse_action(
        'Sure! Here is my next step:\n{"tool": "search_code", '
        '"arguments": {"query": "x"}}\nLet me know if that helps.'
    )

    assert action.tool == "search_code"


def test_a_fenced_code_block_parses() -> None:
    action = parse_action('```json\n{"answer": "It is in auth.py"}\n```')

    assert action.is_final
    assert action.answer == "It is in auth.py"


def test_braces_inside_strings_do_not_break_extraction() -> None:
    action = parse_action('{"tool": "search_code", "arguments": {"query": "a { b }"}}')

    assert action.arguments == {"query": "a { b }"}


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "I cannot help with that.", "{not json at all}", '{"thought": "hm"}'],
)
def test_unusable_replies_report_a_parse_error_rather_than_guessing(raw: str) -> None:
    """Inferring an action the model did not ask for would be worse than failing."""
    action = parse_action(raw)

    assert action.parse_error is not None
    assert action.tool is None
    assert action.answer is None


# --- the loop ---------------------------------------------------------------


def test_a_run_that_answers_immediately_succeeds(repository: uuid.UUID) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel(['{"answer": "verify_state in src/auth.py:1"}'])

    with session_scope() as session:
        outcome = run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            complete=model,
        )

    assert outcome.status is AgentStatus.succeeded
    assert outcome.iterations == 1
    assert "verify_state" in (outcome.answer or "")


def test_a_tool_call_then_an_answer(repository: uuid.UUID) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel([
        '{"tool": "search_symbol", "arguments": {"symbol": "verify_state"}}',
        '{"answer": "It is src/auth.py"}',
    ])

    with session_scope() as session:
        outcome = run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            complete=model,
        )

    assert outcome.status is AgentStatus.succeeded
    assert outcome.iterations == 2
    # The tool result was fed back, so the second prompt is longer.
    assert "verify_state" in model.prompts[1]

    with session_scope() as session:
        calls = list(
            session.execute(select(ToolRun).where(ToolRun.agent_run_id == run_id)).scalars()
        )
    assert [c.tool_name for c in calls] == ["search_symbol"]
    assert calls[0].status is ToolStatus.succeeded


def test_the_iteration_cap_is_hard_and_returns_partial_state(
    repository: uuid.UUID,
) -> None:
    """It must not compose a confident answer from an unfinished investigation."""
    run_id = _make_run(repository, max_iterations=3)
    model = ScriptedModel(['{"tool": "search_symbol", "arguments": {"symbol": "x"}}'])

    with session_scope() as session:
        outcome = run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            complete=model,
        )

    assert outcome.status is AgentStatus.max_iterations_exceeded
    assert outcome.answer is None
    assert outcome.iterations == 3
    assert "3 iterations" in (outcome.error or "")


def test_a_hallucinated_tool_is_refused_and_the_run_continues(
    repository: uuid.UUID,
) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel([
        '{"tool": "delete_everything", "arguments": {}}',
        '{"answer": "I could not use that tool."}',
    ])

    with session_scope() as session:
        outcome = run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            complete=model,
        )

    assert outcome.status is AgentStatus.succeeded
    # The refusal was fed back so the model could choose differently.
    assert "Refused" in model.prompts[1]
    assert "no tool named" in model.prompts[1]

    with session_scope() as session:
        calls = list(
            session.execute(select(ToolRun).where(ToolRun.agent_run_id == run_id)).scalars()
        )
    # Recorded, not discarded: this is the tool-selection metric.
    assert calls[0].tool_name == "delete_everything"
    assert calls[0].status is ToolStatus.rejected


def test_a_path_traversal_attempt_is_refused_and_recorded(
    repository: uuid.UUID, workspace: Path
) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel([
        '{"tool": "read_file", "arguments": {"path": "../../../etc/passwd"}}',
        '{"answer": "Refused, as expected."}',
    ])

    with session_scope() as session:
        run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            workspace=workspace,
            complete=model,
        )

    assert "outside the repository workspace" in model.prompts[1]
    with session_scope() as session:
        calls = list(
            session.execute(select(ToolRun).where(ToolRun.agent_run_id == run_id)).scalars()
        )
    assert calls[0].status is ToolStatus.rejected


def test_a_tool_the_run_lacks_permission_for_is_refused(
    repository: uuid.UUID, workspace: Path
) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel([
        '{"tool": "run_tests", "arguments": {}}',
        '{"answer": "Not permitted."}',
    ])

    with session_scope() as session:
        run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            workspace=workspace,
            granted=frozenset({Permission.repo_read}),
            complete=model,
        )

    assert "sandbox:execute" in model.prompts[1]


def test_unparseable_output_spends_an_iteration_but_does_not_crash(
    repository: uuid.UUID,
) -> None:
    run_id = _make_run(repository, max_iterations=3)
    model = ScriptedModel(["I think the answer is probably in auth.py somewhere."])

    with session_scope() as session:
        outcome = run_agent(
            session,
            session.get(AgentRun, run_id),  # type: ignore[arg-type]
            repository_id=repository,
            complete=model,
        )

    assert outcome.status is AgentStatus.max_iterations_exceeded
    assert outcome.iterations == 3
    # And it was told what was wrong each time.
    assert "could not be used" in model.prompts[1]


# --- tracing ----------------------------------------------------------------


def test_the_trace_records_the_run_in_order(repository: uuid.UUID) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel([
        '{"tool": "search_symbol", "arguments": {"symbol": "verify_state"}}',
        '{"answer": "found"}',
    ])

    with session_scope() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        trace_id = run.trace_id
        run_agent(session, run, repository_id=repository, complete=model)

    with session_scope() as session:
        events = list(
            session.execute(
                select(Event).where(Event.trace_id == trace_id).order_by(Event.sequence)
            ).scalars()
        )

    types = [e.event_type for e in events]
    assert types[0] == "agent.started"
    assert types[-1] == "agent.completed"
    assert "tool.started" in types
    assert "tool.completed" in types
    # Sequence numbers are dense and ordered, so ordering never depends on
    # timestamp resolution.
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))


def test_a_rejected_tool_is_visible_in_the_trace(repository: uuid.UUID) -> None:
    run_id = _make_run(repository)
    model = ScriptedModel([
        '{"tool": "not_a_tool", "arguments": {}}',
        '{"answer": "done"}',
    ])

    with session_scope() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        trace_id = run.trace_id
        run_agent(session, run, repository_id=repository, complete=model)

    with session_scope() as session:
        types = [
            e.event_type
            for e in session.execute(
                select(Event).where(Event.trace_id == trace_id).order_by(Event.sequence)
            ).scalars()
        ]

    assert "tool.rejected" in types
