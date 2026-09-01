"""The tool registry.

Every constraint here holds regardless of what the model asks for, because the
model is not trusted to respect any of them (docs/agents.md):

- **Names resolve against a fixed registry.** The agent cannot invoke anything
  not registered, however convincingly it names it.
- **Permissions are checked in code before execution**, never by prompt
  wording. A prompt instruction is a mitigation; this is the control.
- **Path arguments are resolved and confirmed inside the workspace**, so
  ``../../etc/passwd`` fails at validation rather than at read time.
- **No Stage 1 tool writes** to GitHub or the host filesystem.
- Every call is recorded, including the rejected ones -- a model asking for a
  tool that does not exist is data about tool-selection accuracy, not noise.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger

logger = get_logger(__name__)


class Permission(StrEnum):
    repo_read = "repo:read"
    sandbox_execute = "sandbox:execute"


class ToolError(Exception):
    """A tool refused or failed. Carries a message safe to show the model."""


class ToolRejected(ToolError):
    """The call was not permitted, or its arguments were invalid.

    Distinct from failure: rejection means the tool never ran. The agent is
    told why so it can choose differently, and the attempt is recorded.
    """


@dataclass(slots=True)
class ToolContext:
    """Everything a tool is allowed to reach.

    Passed explicitly rather than read from globals, so what a tool can touch
    is visible in one place and a test can hand it a narrower context.
    """

    session: Session
    repository_id: uuid.UUID
    # The extracted snapshot. None when no workspace has been materialised, in
    # which case filesystem tools reject rather than falling back to the host.
    workspace: Path | None = None
    granted: frozenset[Permission] = frozenset({Permission.repo_read})


@dataclass(slots=True)
class Tool:
    """A registered capability."""

    name: str
    description: str
    permission: Permission
    schema: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], dict[str, Any]]

    def describe(self) -> dict[str, Any]:
        """A JSON description for the prompt."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.schema.model_json_schema(),
        }


# --- argument schemas -------------------------------------------------------


class SearchCodeArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=15)


class ReadFileArgs(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(default=1, ge=1)
    # Bounded so one call cannot pull an entire large file into the context.
    end_line: int | None = Field(default=None, ge=1)


class SearchSymbolArgs(BaseModel):
    symbol: str = Field(min_length=1, max_length=200)


class RepoStructureArgs(BaseModel):
    # Depth-limited: a full tree of a large repository is not useful context,
    # it is a way to exhaust the budget.
    max_depth: int = Field(default=3, ge=1, le=6)


class RunTestsArgs(BaseModel):
    # Not a free-form command. The agent picks a target within the suite; it
    # does not get to choose the interpreter or pass arbitrary flags, because
    # that would make the tool a shell.
    target: str = Field(default="", max_length=500)


# --- path safety ------------------------------------------------------------

MAX_READ_LINES: Final = 400


def resolve_in_workspace(workspace: Path, candidate: str) -> Path:
    """Resolve ``candidate`` and confirm it stays inside ``workspace``.

    Resolution happens first and containment is checked on the *resolved*
    path, so ``..`` segments, absolute paths and symlinks pointing outward are
    all caught by the same check rather than by pattern-matching the input.
    """
    root = workspace.resolve()
    target = (root / candidate).resolve()

    if target != root and root not in target.parents:
        raise ToolRejected(
            f"Path '{candidate}' is outside the repository workspace and cannot be read."
        )
    return target


# --- handlers ---------------------------------------------------------------


def _search_code(context: ToolContext, args: BaseModel) -> dict[str, Any]:
    from app.llm.ollama import OllamaEmbedder
    from app.rag.reranker import RoleWeightedReranker
    from app.rag.retriever import retrieve

    assert isinstance(args, SearchCodeArgs)
    result = retrieve(
        context.session,
        OllamaEmbedder(),
        repository_id=context.repository_id,
        query=args.query,
        limit=args.limit,
        reranker=RoleWeightedReranker(),
    )
    return {
        "results": [
            {
                "file_path": chunk.file_path,
                "symbol": chunk.symbol,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "method": chunk.method.value,
                "content": chunk.content[:1500],
            }
            for chunk in result.chunks
        ],
        "count": len(result.chunks),
    }


def _read_file(context: ToolContext, args: BaseModel) -> dict[str, Any]:
    assert isinstance(args, ReadFileArgs)
    if context.workspace is None:
        raise ToolRejected("No workspace is available, so files cannot be read.")

    target = resolve_in_workspace(context.workspace, args.path)
    if not target.is_file():
        raise ToolRejected(f"'{args.path}' is not a file in this repository.")

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolRejected(f"'{args.path}' could not be read as text.") from exc

    start = args.start_line
    end = args.end_line or (start + MAX_READ_LINES - 1)
    end = min(end, start + MAX_READ_LINES - 1, len(lines))
    excerpt = lines[start - 1 : end]

    return {
        "path": args.path,
        "start_line": start,
        "end_line": start + len(excerpt) - 1 if excerpt else start,
        "total_lines": len(lines),
        # Stated rather than silent: the model must not treat a window as the
        # whole file.
        "truncated": end < len(lines),
        "content": "\n".join(excerpt),
    }


def _search_symbol(context: ToolContext, args: BaseModel) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.chunk import CodeChunk
    from app.models.file import File

    assert isinstance(args, SearchSymbolArgs)
    rows = context.session.execute(
        select(
            File.path, CodeChunk.symbol, CodeChunk.kind,
            CodeChunk.start_line, CodeChunk.end_line, CodeChunk.content,
        )
        .join(File, File.id == CodeChunk.file_id)
        .where(
            # Repository isolation first, as everywhere else.
            CodeChunk.repository_id == context.repository_id,
            CodeChunk.symbol.ilike(f"%{args.symbol}%"),
        )
        .limit(10)
    ).all()

    return {
        "matches": [
            {
                "file_path": row.path,
                "symbol": row.symbol,
                "kind": str(row.kind),
                "start_line": row.start_line,
                "end_line": row.end_line,
                "content": row.content[:1200],
            }
            for row in rows
        ],
        "count": len(rows),
    }


def _repo_structure(context: ToolContext, args: BaseModel) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.file import File

    assert isinstance(args, RepoStructureArgs)
    paths = [
        row.path
        for row in context.session.execute(
            select(File).where(File.repository_id == context.repository_id).order_by(File.path)
        ).scalars()
    ]

    # Built from the index rather than the filesystem: it therefore describes
    # what the agent can actually retrieve, not what happens to be on disk.
    directories: dict[str, int] = {}
    for path in paths:
        parts = path.split("/")[: args.max_depth]
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            directories[prefix] = directories.get(prefix, 0) + 1

    return {
        "file_count": len(paths),
        "directories": [
            {"path": name, "files": count}
            for name, count in sorted(directories.items(), key=lambda item: -item[1])[:40]
        ],
    }


def _run_tests(context: ToolContext, args: BaseModel) -> dict[str, Any]:
    from app.sandbox import run as sandbox_run

    assert isinstance(args, RunTestsArgs)
    if context.workspace is None:
        raise ToolRejected("No workspace is available, so tests cannot be run.")

    # The argument list is constructed here, never taken from the model. The
    # target is passed as a single argv element so it cannot inject flags.
    argv = ["python", "-m", "pytest", "-q", "--no-header"]
    if args.target:
        resolve_in_workspace(context.workspace, args.target)
        argv.append(args.target)

    result = sandbox_run(argv, workspace=context.workspace)
    return {
        "command": " ".join(argv),
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_ms": result.duration_ms,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-2000:],
        "passed": result.succeeded,
    }


# --- the registry -----------------------------------------------------------

REGISTRY: Final[dict[str, Tool]] = {
    tool.name: tool
    for tool in (
        Tool(
            name="search_code",
            description=(
                "Hybrid search over the indexed repository. Use this first for "
                "any question about where something is or how it works."
            ),
            permission=Permission.repo_read,
            schema=SearchCodeArgs,
            handler=_search_code,
        ),
        Tool(
            name="read_file",
            description=(
                "Read a file, or a line range of it, from the repository "
                "snapshot. Use after search_code to see more context."
            ),
            permission=Permission.repo_read,
            schema=ReadFileArgs,
            handler=_read_file,
        ),
        Tool(
            name="search_symbol",
            description="Locate a function or class definition by name.",
            permission=Permission.repo_read,
            schema=SearchSymbolArgs,
            handler=_search_symbol,
        ),
        Tool(
            name="get_repo_structure",
            description="Directory overview of the indexed repository.",
            permission=Permission.repo_read,
            schema=RepoStructureArgs,
            handler=_repo_structure,
        ),
        Tool(
            name="run_tests",
            description=(
                "Run the test suite inside an isolated sandbox. Slow; use only "
                "when a change needs validating."
            ),
            permission=Permission.sandbox_execute,
            schema=RunTestsArgs,
            handler=_run_tests,
        ),
    )
}


def describe_tools(granted: frozenset[Permission]) -> list[dict[str, Any]]:
    """Describe only the tools the caller may actually use.

    Tools the agent has no permission for are not described at all, so it is
    not invited to attempt something that will be refused.
    """
    return [tool.describe() for tool in REGISTRY.values() if tool.permission in granted]


def invoke(context: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve, authorise, validate and execute one tool call.

    The order is the point. Resolution, permission and validation all happen
    before the handler is reached, so an unregistered name, a missing
    permission or a malformed argument can never begin executing anything.
    """
    tool = REGISTRY.get(name)
    if tool is None:
        raise ToolRejected(
            f"There is no tool named '{name}'. Available: {', '.join(sorted(REGISTRY))}."
        )

    if tool.permission not in context.granted:
        logger.warning("tool_permission_denied", tool=name, permission=tool.permission.value)
        raise ToolRejected(f"Tool '{name}' requires the {tool.permission.value} permission.")

    try:
        parsed = tool.schema.model_validate(arguments)
    except ValidationError as exc:
        raise ToolRejected(
            f"Invalid arguments for '{name}': {exc.error_count()} problem(s)."
        ) from exc

    return tool.handler(context, parsed)
