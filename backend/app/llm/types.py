"""Types shared by every chat provider.

Separate from ``chat.py`` so a provider can import the result type without
importing the prompt-building module that imports providers. The alternative is
a circular import resolved by a deferred import inside a function, which hides
the dependency rather than removing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class ChatCompletion:
    answer: str
    model: str
    # Reported so a slow answer is attributable rather than mysterious.
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ChatProvider(Protocol):
    """Produces one answer from a system prompt and a user prompt.

    A Protocol rather than a base class, matching ``EmbeddingProvider``: a test
    double needs no inheritance, and a provider need not import from here.

    The interface is deliberately small. Streaming, tool calling and structured
    output are absent because nothing in this codebase uses them -- the agent
    parses its actions out of text so that it works with a local model that
    offers none of those. Adding them to the interface before a caller needs
    them would be designing for an imagined second implementation.
    """

    @property
    def name(self) -> str:
        """Short identifier, recorded so an answer is attributable."""
        ...

    @property
    def model_name(self) -> str:
        """The model this provider will use when none is passed."""
        ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> ChatCompletion:
        """Return one completed answer, or raise ``ExternalServiceError``.

        Implementations must never return an empty answer. An empty string is
        indistinguishable at the call site from a model with nothing to say,
        and both callers here treat an answer as evidence of work done.
        """
        ...
