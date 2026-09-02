"""Labelled tasks for the agent, and the Stage 1 baseline they establish.

ADR-007 makes this the entry condition for Stage 2: agent metrics have to be
*recorded* so that any claim multi-agent is better can be checked rather than
asserted. This file is the ground truth those metrics are computed against.

The tasks are questions a developer would actually ask about this codebase,
each with the file and symbol that answers it. They deliberately span the two
shapes the agent handles differently:

``lookup``
    The answer is one symbol in one file. A single ``search_code`` or
    ``search_symbol`` should be enough, so extra iterations are waste.
``investigation``
    The answer needs more than one place, or reading a file after finding it.
    Several tool calls are the *correct* behaviour here, so iteration count
    must be read against the task shape rather than treated as uniformly bad.

Written before any run was scored, and not adjusted afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskShape(StrEnum):
    lookup = "lookup"
    investigation = "investigation"


@dataclass(frozen=True, slots=True)
class AgentTask:
    id: str
    task: str
    # Success is judged on these. Either a file or a symbol counts, because an
    # answer naming ``chunk_source`` without its path has still found it.
    expected_files: frozenset[str]
    expected_symbols: frozenset[str]
    shape: TaskShape
    # Tools that could reasonably answer this. Used for tool-selection
    # accuracy, and kept generous: there is usually more than one right way in,
    # and penalising a defensible route would measure conformity, not skill.
    reasonable_tools: frozenset[str]
    rationale: str


def _t(
    id: str,
    task: str,
    files: list[str],
    symbols: list[str],
    shape: TaskShape,
    tools: list[str],
    rationale: str,
) -> AgentTask:
    return AgentTask(
        id=id,
        task=task,
        expected_files=frozenset(files),
        expected_symbols=frozenset(symbols),
        shape=shape,
        reasonable_tools=frozenset(tools),
        rationale=rationale,
    )


_SEARCH = ["search_code", "search_symbol"]
_SEARCH_READ = ["search_code", "search_symbol", "read_file"]
_ALL_READ = ["search_code", "search_symbol", "read_file", "get_repo_structure"]

AGENT_BENCHMARK: list[AgentTask] = [
    _t(
        "a-01",
        "Which function handles the GitHub OAuth callback, and in which file?",
        ["backend/app/routes/auth.py"],
        ["github_callback"],
        TaskShape.lookup,
        _SEARCH,
        "One handler, one file.",
    ),
    _t(
        "a-02",
        "Where are GitHub access tokens encrypted before being stored?",
        ["backend/app/core/security.py"],
        ["encrypt_token"],
        TaskShape.lookup,
        _SEARCH,
        "Fernet encryption at rest.",
    ),
    _t(
        "a-03",
        "Which function splits a source file into chunks?",
        ["backend/app/ingestion/chunker.py"],
        ["chunk_source"],
        TaskShape.lookup,
        _SEARCH,
        "The chunker entry point.",
    ),
    _t(
        "a-04",
        "What stops secret files such as .env from being indexed?",
        ["backend/app/ingestion/filters.py"],
        ["is_secret_path", "classify"],
        TaskShape.lookup,
        _SEARCH,
        "The secret exclusion is a named function.",
    ),
    _t(
        "a-05",
        "Which function merges the vector and keyword search results?",
        ["backend/app/rag/fusion.py"],
        ["reciprocal_rank_fusion"],
        TaskShape.lookup,
        _SEARCH,
        "One fusion implementation.",
    ),
    _t(
        "a-06",
        "Which function decides whether a file is source, test or documentation?",
        ["backend/app/rag/roles.py"],
        ["classify_role"],
        TaskShape.lookup,
        _SEARCH,
        "Role classification.",
    ),
    _t(
        "a-07",
        "Where is the agent's maximum iteration limit enforced?",
        ["backend/app/agent/engine.py"],
        ["run_agent"],
        TaskShape.lookup,
        _SEARCH,
        "The loop bound lives in the loop.",
    ),
    _t(
        "a-08",
        "Which function checks that a tool's path argument stays inside the workspace?",
        ["backend/app/agent/tools.py"],
        ["resolve_in_workspace"],
        TaskShape.lookup,
        _SEARCH,
        "Path containment.",
    ),
    # --- investigation ------------------------------------------------------
    _t(
        "a-09",
        "How does the sandbox stop code it runs from reaching the network? "
        "Name the file and the mechanism.",
        ["backend/app/sandbox/runner.py"],
        ["build_command"],
        TaskShape.investigation,
        _SEARCH_READ,
        "Needs the flag list, which is inside a function body rather than in "
        "its name -- a search alone may find the file but not the mechanism.",
    ),
    _t(
        "a-10",
        "How does re-indexing avoid re-embedding chunks that have not changed?",
        [
            "backend/app/ingestion/service.py",
            "backend/app/ingestion/embedder.py",
        ],
        ["embed_repository", "_persist"],
        TaskShape.investigation,
        _SEARCH_READ,
        "The answer spans the content hash in the service and the pending "
        "query in the embedder.",
    ),
    _t(
        "a-11",
        "How is a proposed patch validated before a human sees it?",
        ["backend/app/agent/patches.py"],
        ["validate_patch", "parse_patch"],
        TaskShape.investigation,
        _SEARCH_READ,
        "Parse, then apply in the sandbox, then test -- more than one step.",
    ),
    _t(
        "a-12",
        "What prevents one user from retrieving another user's code?",
        [
            "backend/app/rag/vector.py",
            "backend/app/rag/keyword.py",
            "backend/app/routes/repositories.py",
        ],
        ["search", "_owned_repository"],
        TaskShape.investigation,
        _ALL_READ,
        "Tenant isolation is enforced in several places; naming any is a hit.",
    ),
]


def expected_paths() -> set[str]:
    """Every labelled path, for the staleness check."""
    return {path for task in AGENT_BENCHMARK for path in task.expected_files}
