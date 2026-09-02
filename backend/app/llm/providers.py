"""Selects the chat provider named by configuration.

One function, so there is exactly one place that decides which model answers.
Routes and the agent loop ask for a completion and get whatever
``LLM_PROVIDER`` names; neither knows or chooses.

Not cached. ``get_settings`` is, and a provider is a thin object over an httpx
call, so caching this would buy nothing and would make a settings change in a
test invisible.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.llm.gemini import GeminiChatProvider
from app.llm.ollama_chat import OllamaChatProvider
from app.llm.types import ChatProvider

# Providers that send repository content off this machine. Membership is a
# privacy fact rather than a deployment detail: a self-hosted Ollama is on the
# operator's own hardware, a hosted API is not.
REMOTE_PROVIDERS: frozenset[str] = frozenset({"gemini"})


def get_chat_provider() -> ChatProvider:
    """Return the configured chat provider, ignoring repository consent.

    For callers with no repository in hand -- a health check, a CLI probe. Any
    path that sends *repository content* must use ``resolve_chat_provider``
    instead, so the choice is made against that repository's permission.
    """
    settings = get_settings()
    if settings.llm_provider == "gemini":
        return GeminiChatProvider(settings)
    return OllamaChatProvider(settings)


@dataclass(frozen=True)
class ProviderChoice:
    """Which provider will answer, and why it might not be the configured one."""

    provider: ChatProvider
    # True when a remote provider was configured but the repository has not
    # opted in, so the local model answered instead.
    downgraded: bool = False

    @property
    def note(self) -> str | None:
        """A sentence for the caller to show the user, or None.

        A silent downgrade is the failure mode to avoid. The answer would be
        visibly worse for a reason the user cannot see, and the natural
        conclusion -- that the system is bad at this -- would be wrong.
        """
        if not self.downgraded:
            return None
        return (
            "This repository has not been opted in to a cloud model, so the "
            "local model answered instead. Answers may be less reliable and "
            "cite fewer sources. Enable it for this repository if you are "
            "willing for its retrieved code to be sent to the provider."
        )


def resolve_chat_provider(*, allow_cloud: bool) -> ProviderChoice:
    """Pick the provider allowed to see one repository's content.

    ``allow_cloud`` comes from the repository row, never from configuration.
    Configuration says which cloud provider is available; the repository says
    whether it may be used. Both must agree, and the repository is the one that
    can say no.

    Falling back rather than refusing is deliberate. Refusing would make a
    repository unusable until someone changed a setting, which pushes people
    toward granting permission to get their tool working -- consent extracted by
    obstruction. The local model always works and discloses nothing, so it is
    the safe default and the downgrade is reported rather than hidden.
    """
    settings = get_settings()
    if settings.llm_provider not in REMOTE_PROVIDERS:
        return ProviderChoice(provider=OllamaChatProvider(settings))
    if not allow_cloud:
        return ProviderChoice(provider=OllamaChatProvider(settings), downgraded=True)
    return ProviderChoice(provider=GeminiChatProvider(settings))
