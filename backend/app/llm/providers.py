"""Selects the chat provider named by configuration.

One function, so there is exactly one place that decides which model answers.
Routes and the agent loop ask for a completion and get whatever
``LLM_PROVIDER`` names; neither knows or chooses.

Not cached. ``get_settings`` is, and a provider is a thin object over an httpx
call, so caching this would buy nothing and would make a settings change in a
test invisible.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.llm.gemini import GeminiChatProvider
from app.llm.ollama_chat import OllamaChatProvider
from app.llm.types import ChatProvider


def get_chat_provider() -> ChatProvider:
    """Return the configured chat provider."""
    settings = get_settings()
    if settings.llm_provider == "gemini":
        return GeminiChatProvider(settings)
    return OllamaChatProvider(settings)
