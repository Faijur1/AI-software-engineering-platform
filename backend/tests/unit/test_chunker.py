"""AST-aware chunking.

The property that matters is not "it produced N chunks" but that each chunk is
a whole construct with line numbers pointing at where it really is -- because a
chunk is both the LLM's context and the user's citation (ADR-002).
"""

from __future__ import annotations

import textwrap

from app.ingestion.chunker import (
    MAX_CHUNK_CHARS,
    Chunk,
    chunk_source,
)
from app.models.chunk import ChunkKind

PYTHON = textwrap.dedent(
    '''
    import os
    from pathlib import Path

    TIMEOUT = 30

    def standalone(a, b):
        """Add two things."""
        return a + b

    class Service:
        def __init__(self):
            self.value = 1

        def compute(self):
            return self.value * 2

    async def entrypoint():
        return None
    '''
).strip()


def _by_symbol(chunks: list[Chunk]) -> dict[str, Chunk]:
    return {c.symbol: c for c in chunks if c.symbol}


def test_functions_are_whole_chunks_with_correct_lines() -> None:
    chunks = chunk_source(PYTHON, "python")
    found = _by_symbol(chunks)

    assert "standalone" in found
    fn = found["standalone"]
    assert fn.kind is ChunkKind.function
    # The whole definition, signature through body.
    assert fn.content.startswith("def standalone(a, b):")
    assert "return a + b" in fn.content
    # And the line numbers point at it in the original file.
    lines = PYTHON.splitlines()
    assert lines[fn.start_line - 1].startswith("def standalone")
    assert lines[fn.end_line - 1].strip() == "return a + b"


def test_line_numbers_always_map_back_to_the_source() -> None:
    """Every chunk's span must be findable in the file it came from.

    This is what makes a citation trustworthy; an off-by-one here points the
    user at the wrong code.
    """
    for language, source in (("python", PYTHON), ("typescript", TYPESCRIPT)):
        lines = source.splitlines()
        for chunk in chunk_source(source, language):
            assert 1 <= chunk.start_line <= chunk.end_line <= len(lines), chunk
            excerpt = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
            assert chunk.content.strip() in excerpt or excerpt.strip() in chunk.content


def test_small_classes_are_kept_whole() -> None:
    found = _by_symbol(chunk_source(PYTHON, "python"))
    assert "Service" in found
    service = found["Service"]
    assert service.kind is ChunkKind.class_
    # Both methods travel with the class, because the class is small enough to
    # be the useful unit.
    assert "def compute" in service.content
    assert "def __init__" in service.content


def test_large_classes_are_split_into_methods() -> None:
    """Above the size threshold the class is indexed method by method."""
    body = "\n\n".join(
        f"    def method_{i}(self):\n"
        f'        """Documentation for method {i}."""\n'
        f"        return {i} * self.factor + len(str({i}))" for i in range(40)
    )
    source = f"class Big:\n    factor = 2\n\n{body}\n"

    chunks = chunk_source(source, "python")
    symbols = {c.symbol for c in chunks if c.symbol}

    assert "Big.method_0" in symbols
    assert "Big.method_39" in symbols
    # Methods are labelled as such, which is what the qualified name signals.
    methods = [c for c in chunks if c.symbol == "Big.method_5"]
    assert methods and methods[0].kind is ChunkKind.method


def test_module_level_statements_become_one_block() -> None:
    chunks = chunk_source(PYTHON, "python")
    blocks = [c for c in chunks if c.kind is ChunkKind.block]

    assert blocks, "imports and constants must be indexed, not dropped"
    combined = "\n".join(b.content for b in blocks)
    assert "import os" in combined
    assert "TIMEOUT = 30" in combined


def test_decorators_stay_attached_to_their_function() -> None:
    """A route's decorator carries its path; separating them loses the meaning."""
    source = textwrap.dedent(
        '''
        @router.get("/health")
        def health_check():
            return {"status": "ok"}
        '''
    ).strip()

    found = _by_symbol(chunk_source(source, "python"))
    assert "health_check" in found
    assert '@router.get("/health")' in found["health_check"].content


TYPESCRIPT = textwrap.dedent(
    """
    import { useState } from "react";

    export interface Props {
      id: string;
    }

    export function useThing(id: string) {
      const [value, setValue] = useState(id);
      return value;
    }

    export default function Page() {
      return null;
    }
    """
).strip()


def test_exported_declarations_are_unwrapped() -> None:
    """``export function f`` must be indexed as f, not as an export statement."""
    found = _by_symbol(chunk_source(TYPESCRIPT, "typescript"))

    assert "useThing" in found
    assert found["useThing"].content.startswith("export function useThing")
    assert "Page" in found
    assert "Props" in found


def test_oversized_functions_are_split_into_labelled_fragments() -> None:
    body = "\n".join(f"    value_{i} = compute_something({i}) + offset_{i}" for i in range(400))
    source = f"def enormous():\n{body}\n"

    chunks = chunk_source(source, "python")

    assert len(chunks) > 1
    assert all(c.kind is ChunkKind.fragment for c in chunks)
    # Labelled so evaluation can measure fragments separately from whole units.
    assert chunks[0].symbol == "enormous (part 1)"
    assert all(len(c.content) <= MAX_CHUNK_CHARS * 2 for c in chunks)


def test_files_without_a_grammar_fall_back_to_size_chunks() -> None:
    markdown = "\n".join(f"line {i} of documentation prose" for i in range(200))

    chunks = chunk_source(markdown, None)

    assert chunks
    assert all(c.kind is ChunkKind.fallback for c in chunks)
    assert all(c.symbol is None for c in chunks)


def test_fallback_chunks_overlap_so_nothing_falls_between_them() -> None:
    lines = [f"line {i}" for i in range(150)]
    chunks = chunk_source("\n".join(lines), None)

    assert len(chunks) > 1
    # Consecutive windows overlap, so a construct on a boundary survives whole
    # in at least one of them.
    assert chunks[1].start_line < chunks[0].end_line


def test_unparseable_source_degrades_instead_of_raising() -> None:
    """One broken file must not fail the index for a whole repository."""
    broken = "def (((( this is not python at all ][}\n" * 30

    chunks = chunk_source(broken, "python")

    assert chunks
    assert all(c.content for c in chunks)


def test_empty_and_whitespace_files_produce_nothing() -> None:
    assert chunk_source("", "python") == []
    assert chunk_source("   \n\n  \t\n", "python") == []
    assert chunk_source("", None) == []


def test_chunk_hash_is_content_addressed() -> None:
    """Equal content hashes equally; that is what makes re-indexing skippable."""
    a = Chunk("def f(): pass", ChunkKind.function, 1, 1, "f")
    b = Chunk("def f(): pass", ChunkKind.function, 99, 99, "different")
    c = Chunk("def g(): pass", ChunkKind.function, 1, 1, "f")

    assert a.chunk_hash == b.chunk_hash
    assert a.chunk_hash != c.chunk_hash
    assert len(a.chunk_hash) == 64


def test_no_chunk_is_empty_or_whitespace_only() -> None:
    for language, source in (("python", PYTHON), ("typescript", TYPESCRIPT), (None, "a\n\n\nb")):
        for chunk in chunk_source(source, language):
            assert chunk.content.strip(), f"empty chunk from {language}"
