"""Split source text into logical units for retrieval.

Chunks are functions, methods, classes and module-level blocks -- not fixed
character windows (ADR-002). The unit matters because a chunk is both what the
LLM receives as context and what the user is shown as a citation: half a
function is misleading in both roles.

Three degradations, each named in the output so evaluation can measure them
separately rather than averaging them into the good case:

``fallback``  the file has no grammar, so it is split by size
``fragment``  a single unit exceeded the budget and had to be split
``block``     a run of module-level statements with no enclosing definition
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from tree_sitter import Node

from app.ingestion.languages import get_language_parser
from app.models.chunk import ChunkKind

# Roughly 1000 tokens of code: large enough to hold most real functions whole,
# small enough that several chunks fit in a context window beside a prompt.
MAX_CHUNK_CHARS: Final = 4000
# A class below this size stays whole, because the class body is then the
# useful unit; above it, its methods are indexed individually instead.
MAX_WHOLE_CLASS_CHARS: Final = 2500
FALLBACK_CHUNK_LINES: Final = 60
# Size-based splitting overlaps slightly, so a construct straddling a boundary
# is still wholly present in one of the two chunks.
FALLBACK_OVERLAP_LINES: Final = 5

# Node types that are a callable definition, across the grammars in use.
_FUNCTION_NODES: Final[frozenset[str]] = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_item",
        "method_definition",
        "method_declaration",
        "constructor_declaration",
        "generator_function_declaration",
        "func_literal",
    }
)

# Node types that group definitions: classes, structs, interfaces, impls.
_CONTAINER_NODES: Final[frozenset[str]] = frozenset(
    {
        "class_definition",
        "class_declaration",
        "class_specifier",
        "struct_item",
        "struct_specifier",
        "impl_item",
        "trait_item",
        "interface_declaration",
        "enum_declaration",
        "enum_item",
        "object_declaration",
        "namespace_definition",
        "protocol_declaration",
        "extension_declaration",
    }
)

# Wrappers carrying a definition inside them. The definition is the real unit,
# but the wrapper's span is what to capture: decorators, export keywords and
# modifiers all belong with the code they apply to.
_WRAPPER_NODES: Final[frozenset[str]] = frozenset(
    {
        "decorated_definition",
        "export_statement",
        "ambient_declaration",
    }
)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit of code. Line numbers are 1-based and inclusive."""

    content: str
    kind: ChunkKind
    start_line: int
    end_line: int
    symbol: str | None = None

    @property
    def chunk_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def chunk_source(content: str, language: str | None) -> list[Chunk]:
    """Split ``content`` into chunks, by AST where a grammar allows it.

    Never raises for malformed input. A file that fails to parse falls back to
    size-based splitting, because one unparseable file must not fail the index
    for an entire repository.
    """
    if not content.strip():
        return []

    parser = get_language_parser(language) if language else None
    if parser is None:
        return _chunk_by_size(content)

    try:
        source = content.encode("utf-8")
        root = parser.parse(source).root_node
    except Exception:
        return _chunk_by_size(content)

    # tree-sitter is error-tolerant and returns a tree even for broken source.
    # A tree that is entirely an error node carries no structure worth using.
    if root.child_count == 0 or root.type == "ERROR":
        return _chunk_by_size(content)

    chunks = _chunk_children(root, source, prefix=None)
    return chunks if chunks else _chunk_by_size(content)


def _chunk_children(parent: Node, source: bytes, prefix: str | None) -> list[Chunk]:
    """Chunk the children of ``parent``, grouping loose statements into blocks."""
    chunks: list[Chunk] = []
    pending: list[Node] = []

    def flush() -> None:
        """Emit accumulated loose statements as one module-level block."""
        if not pending:
            return
        text = _span(source, pending[0].start_byte, pending[-1].end_byte)
        if text.strip():
            chunks.extend(_emit(text, ChunkKind.block, pending[0], pending[-1], None))
        pending.clear()

    for child in parent.children:
        definition, kind = _classify(child)
        if definition is None:
            pending.append(child)
            continue

        flush()
        name = _name_of(definition)
        symbol = f"{prefix}.{name}" if prefix and name else name
        text = _span(source, child.start_byte, child.end_byte)

        if kind is ChunkKind.class_ and len(text) > MAX_WHOLE_CLASS_CHARS:
            # Too large to be useful whole: index its methods individually, so
            # a retrieved chunk is a method someone can read rather than a wall
            # of text that crowds out everything else in the context window.
            body = definition.child_by_field_name("body")
            inner = _chunk_children(body, source, symbol) if body is not None else []
            if inner:
                chunks.extend(inner)
                continue

        chunks.extend(_emit(text, kind, child, child, symbol))

    flush()
    return chunks


def _classify(node: Node) -> tuple[Node | None, ChunkKind]:
    """Return the definition inside ``node`` and its kind, or ``(None, ...)``.

    Unwraps decorators and export wrappers, so ``@app.get(...)`` and
    ``export default function`` stay attached to what they apply to.
    """
    target = node
    if node.type in _WRAPPER_NODES:
        inner = next(
            (c for c in node.children if c.type in _FUNCTION_NODES | _CONTAINER_NODES),
            None,
        )
        if inner is None:
            return None, ChunkKind.block
        target = inner

    if target.type in _FUNCTION_NODES:
        return target, ChunkKind.function
    if target.type in _CONTAINER_NODES:
        return target, ChunkKind.class_
    return None, ChunkKind.block


def _name_of(node: Node) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None and named.text is not None:
        return named.text.decode("utf-8", errors="replace")
    # Rust impl blocks and similar name themselves through a type field.
    typed = node.child_by_field_name("type")
    if typed is not None and typed.text is not None:
        return typed.text.decode("utf-8", errors="replace")
    return None


def _emit(
    text: str, kind: ChunkKind, first: Node, last: Node, symbol: str | None
) -> list[Chunk]:
    """Emit one chunk, splitting it into fragments if it is oversized."""
    start = first.start_point[0] + 1
    end = last.end_point[0] + 1

    if len(text) <= MAX_CHUNK_CHARS:
        # A qualified symbol means the function sits inside a class, which is
        # what distinguishes a method from a free function.
        resolved = kind
        if kind is ChunkKind.function and symbol is not None and "." in symbol:
            resolved = ChunkKind.method
        return [Chunk(text, resolved, start, end, symbol)]

    fragments: list[Chunk] = []
    windows = _split_lines(text, FALLBACK_CHUNK_LINES, 0)
    for index, (offset, body) in enumerate(windows, start=1):
        fragments.append(
            Chunk(
                body,
                ChunkKind.fragment,
                start + offset,
                start + offset + body.count("\n"),
                f"{symbol} (part {index})" if symbol else None,
            )
        )
    return fragments


def _chunk_by_size(content: str) -> list[Chunk]:
    """Split by line count, for files with no grammar or no usable parse."""
    return [
        Chunk(body, ChunkKind.fallback, offset + 1, offset + 1 + body.count("\n"), None)
        for offset, body in _split_lines(content, FALLBACK_CHUNK_LINES, FALLBACK_OVERLAP_LINES)
    ]


def _split_lines(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """Split into ``(line offset, text)`` windows of at most ``size`` lines."""
    lines = text.splitlines()
    if not lines:
        return []
    step = max(size - overlap, 1)
    out: list[tuple[int, str]] = []
    for start in range(0, len(lines), step):
        body = "\n".join(lines[start : start + size])
        if body.strip():
            out.append((start, body))
        if start + size >= len(lines):
            break
    return out


def _span(source: bytes, start: int, end: int) -> str:
    return source[start:end].decode("utf-8", errors="replace")
