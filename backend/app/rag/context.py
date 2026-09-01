"""Assemble retrieved chunks into a prompt context.

Three jobs, and the last one is a security control rather than a formatting
choice:

1. **Enforce a budget.** A context that overflows is silently truncated by the
   model, usually from the end, which quietly drops the evidence an answer was
   supposed to rest on.
2. **Preserve file and line metadata**, so every claim can be traced to real
   code. A citation that cannot be checked is decoration.
3. **Label repository content as untrusted data.** A file that says "ignore
   previous instructions" is text being summarised, not an instruction
   (docs/security.md). The prompt wording is a mitigation, not the control --
   the actual control is that nothing in Stage 1 can act on model output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.rag.types import RetrievedChunk

# Characters per token. A rough approximation, and deliberately labelled as
# one: the real tokeniser is the model's, and shipping a second tokeniser to
# predict it would add a dependency and still be an estimate. Code tokenises
# less efficiently than prose, so 3.5 errs on the safe side.
CHARS_PER_TOKEN: Final = 3.5
# Leaves room for the system prompt, the question, and the answer itself inside
# a typical 8k window.
DEFAULT_TOKEN_BUDGET: Final = 4000
# A chunk larger than this is truncated rather than dropped: half a large
# function is still evidence, and dropping it wastes the retrieval.
MAX_CHUNK_TOKENS: Final = 900


@dataclass(frozen=True, slots=True)
class Source:
    """One cited piece of evidence, as the client will render it."""

    index: int
    chunk_id: str
    file_path: str
    symbol: str | None
    start_line: int
    end_line: int
    content: str


@dataclass(slots=True)
class BuiltContext:
    prompt_context: str
    sources: list[Source]
    # Counted, not estimated: how many candidates were offered and how many
    # actually fitted. A silently half-used context is a bug worth seeing.
    offered: int
    included: int
    estimated_tokens: int
    dropped_for_budget: int


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text``. See CHARS_PER_TOKEN."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def build_context(
    chunks: list[RetrievedChunk], *, token_budget: int = DEFAULT_TOKEN_BUDGET
) -> BuiltContext:
    """Turn ranked chunks into a numbered, delimited context block.

    Chunks are added in rank order until the budget is reached, so the best
    evidence is the evidence that survives. Sources are numbered from 1 and the
    model is asked to cite those numbers, which keeps citations checkable: an
    index either maps to a real retrieved chunk or it does not.
    """
    sources: list[Source] = []
    blocks: list[str] = []
    used = 0
    dropped = 0

    for chunk in chunks:
        content = _truncate(chunk.content)
        index = len(sources) + 1
        block = _format_block(index, chunk, content)
        cost = estimate_tokens(block)

        if used + cost > token_budget:
            # Keep scanning rather than breaking: a later, smaller chunk may
            # still fit, and leaving budget unused helps nobody.
            dropped += 1
            continue

        used += cost
        sources.append(
            Source(
                index=index,
                chunk_id=str(chunk.chunk_id),
                file_path=chunk.file_path,
                symbol=chunk.symbol,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=content,
            )
        )
        blocks.append(block)

    return BuiltContext(
        prompt_context="\n\n".join(blocks),
        sources=sources,
        offered=len(chunks),
        included=len(sources),
        estimated_tokens=used,
        dropped_for_budget=dropped,
    )


def _format_block(index: int, chunk: RetrievedChunk, content: str) -> str:
    """Render one source with an explicit, greppable delimiter.

    The delimiters matter: they mark where untrusted repository content starts
    and stops, so instructions embedded in a file are visibly inside a data
    region rather than adjacent to the real instructions.
    """
    location = f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
    heading = f"[{index}] {location}"
    if chunk.symbol:
        heading += f"  ({chunk.symbol})"
    return f"<<<SOURCE {index}\n{heading}\n---\n{content}\nSOURCE {index}>>>"


def _truncate(content: str) -> str:
    limit = int(MAX_CHUNK_TOKENS * CHARS_PER_TOKEN)
    if len(content) <= limit:
        return content
    # Truncation is stated in the text so the model does not treat a cut-off
    # function as the whole thing.
    return content[:limit] + "\n... (truncated)"
