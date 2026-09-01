"""Tool registry guardrails.

These are the constraints that hold *whatever the model asks for*. The local
model is weak and will ask for wrong things; that is exactly why none of this
is enforced by prompt wording. Every test here asks the registry to do
something it must refuse.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agent.tools import (
    REGISTRY,
    Permission,
    ToolContext,
    ToolRejected,
    describe_tools,
    invoke,
    resolve_in_workspace,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 51)), encoding="utf-8"
    )
    (tmp_path / "secret.bin").write_bytes(b"\xff\xfe\x00binary")
    return tmp_path


def _context(workspace: Path | None = None, **kwargs: Any) -> ToolContext:
    return ToolContext(
        session=None,  # type: ignore[arg-type]
        repository_id=uuid.uuid4(),
        workspace=workspace,
        **kwargs,
    )


# --- the registry is closed -------------------------------------------------


def test_an_unregistered_tool_cannot_be_invoked() -> None:
    """However convincingly the model names it."""
    with pytest.raises(ToolRejected, match="no tool named"):
        invoke(_context(), "delete_repository", {})


def test_the_rejection_names_what_is_available() -> None:
    """A refusal the agent can act on beats one it can only retry."""
    with pytest.raises(ToolRejected, match="search_code"):
        invoke(_context(), "definitely_not_a_tool", {})


def test_no_registered_tool_writes_anywhere() -> None:
    """Stage 1 has no write path, by construction (docs/agents.md)."""
    assert set(REGISTRY) == {
        "search_code",
        "read_file",
        "search_symbol",
        "get_repo_structure",
        "run_tests",
    }


# --- permissions are checked in code ----------------------------------------


def test_a_tool_without_its_permission_is_refused() -> None:
    context = _context(granted=frozenset({Permission.repo_read}))

    with pytest.raises(ToolRejected, match="sandbox:execute"):
        invoke(context, "run_tests", {})


def test_permission_is_checked_before_arguments_are_even_valid() -> None:
    """Refusal must not depend on the call being well-formed."""
    context = _context(granted=frozenset({Permission.repo_read}))

    with pytest.raises(ToolRejected, match="permission"):
        invoke(context, "run_tests", {"target": 12345})


def test_only_permitted_tools_are_described_to_the_model() -> None:
    """Do not invite an attempt that will be refused."""
    read_only = describe_tools(frozenset({Permission.repo_read}))
    with_sandbox = describe_tools(
        frozenset({Permission.repo_read, Permission.sandbox_execute})
    )

    assert "run_tests" not in {tool["name"] for tool in read_only}
    assert "run_tests" in {tool["name"] for tool in with_sandbox}


def test_every_tool_declares_a_schema_and_a_permission() -> None:
    for name, tool in REGISTRY.items():
        assert tool.permission in Permission, name
        assert tool.describe()["parameters"]["type"] == "object", name
        assert tool.description.strip(), name


# --- path containment -------------------------------------------------------


@pytest.mark.parametrize(
    "attempt",
    [
        "../../etc/passwd",
        "../outside.txt",
        "src/../../escape.py",
        "/etc/passwd",
        "src/../../../root/.ssh/id_rsa",
    ],
)
def test_paths_outside_the_workspace_are_rejected(workspace: Path, attempt: str) -> None:
    """Checked on the *resolved* path, so traversal cannot be spelled around."""
    with pytest.raises(ToolRejected, match="outside the repository workspace"):
        resolve_in_workspace(workspace, attempt)


def test_a_path_inside_the_workspace_resolves(workspace: Path) -> None:
    resolved = resolve_in_workspace(workspace, "src/main.py")

    assert resolved == (workspace / "src" / "main.py").resolve()


def test_read_file_refuses_to_escape_the_workspace(workspace: Path) -> None:
    with pytest.raises(ToolRejected, match="outside"):
        invoke(_context(workspace), "read_file", {"path": "../../../etc/passwd"})


def test_reading_without_a_workspace_refuses_rather_than_using_the_host(
    workspace: Path,
) -> None:
    """No workspace must never mean "read from wherever the process is"."""
    with pytest.raises(ToolRejected, match="No workspace"):
        invoke(_context(None), "read_file", {"path": "src/main.py"})


def test_running_tests_without_a_workspace_refuses(workspace: Path) -> None:
    context = _context(
        None, granted=frozenset({Permission.repo_read, Permission.sandbox_execute})
    )

    with pytest.raises(ToolRejected, match="No workspace"):
        invoke(context, "run_tests", {})


# --- argument validation ----------------------------------------------------


def test_malformed_arguments_are_rejected_before_execution() -> None:
    with pytest.raises(ToolRejected, match="Invalid arguments"):
        invoke(_context(), "search_code", {"query": ""})


def test_limits_are_bounded_so_one_call_cannot_drain_the_budget() -> None:
    with pytest.raises(ToolRejected, match="Invalid arguments"):
        invoke(_context(), "search_code", {"query": "x", "limit": 500})


def test_missing_required_arguments_are_rejected() -> None:
    with pytest.raises(ToolRejected, match="Invalid arguments"):
        invoke(_context(), "search_symbol", {})


# --- reading behaves ---------------------------------------------------------


def test_reading_a_range_reports_that_it_is_a_range(workspace: Path) -> None:
    """The model must not treat a window as the whole file."""
    output = invoke(
        _context(workspace), "read_file", {"path": "src/main.py", "start_line": 5, "end_line": 9}
    )

    assert output["content"].splitlines() == [f"line {i}" for i in range(5, 10)]
    assert output["total_lines"] == 50
    assert output["truncated"] is True


def test_a_whole_small_file_is_not_marked_truncated(workspace: Path) -> None:
    output = invoke(_context(workspace), "read_file", {"path": "src/main.py"})

    assert output["total_lines"] == 50
    assert output["truncated"] is False


def test_a_binary_file_is_refused_not_mangled(workspace: Path) -> None:
    with pytest.raises(ToolRejected, match="could not be read as text"):
        invoke(_context(workspace), "read_file", {"path": "secret.bin"})


def test_a_missing_file_is_refused_with_a_usable_message(workspace: Path) -> None:
    with pytest.raises(ToolRejected, match="not a file"):
        invoke(_context(workspace), "read_file", {"path": "src/nope.py"})
