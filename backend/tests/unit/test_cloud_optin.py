"""Per-repository consent to a cloud model provider.

The property under test is a privacy one: repository content must not reach a
hosted API unless that repository's owner said it may. Configuration alone is
never enough, and the default is deny.

These are the tests that would fail if someone "simplified" the resolution back
to reading the global setting, which is exactly the change that would look
harmless in review.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.llm.gemini import GeminiChatProvider
from app.llm.ollama_chat import OllamaChatProvider
from app.llm.providers import REMOTE_PROVIDERS, resolve_chat_provider


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    get_settings.cache_clear()


# --- the core rule ----------------------------------------------------------


def test_a_repository_that_has_not_opted_in_never_reaches_the_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole feature in one assertion."""
    _configure(monkeypatch, "gemini")

    choice = resolve_chat_provider(allow_cloud=False)

    assert isinstance(choice.provider, OllamaChatProvider)
    assert choice.downgraded is True


def test_an_opted_in_repository_uses_the_configured_cloud_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "gemini")

    choice = resolve_chat_provider(allow_cloud=True)

    assert isinstance(choice.provider, GeminiChatProvider)
    assert choice.downgraded is False


def test_opting_in_does_nothing_when_no_cloud_provider_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consent is permission, not instruction.

    A repository marked allowed while the deployment runs entirely locally must
    still be answered locally -- and must not be reported as downgraded, since
    nothing was withheld.
    """
    _configure(monkeypatch, "ollama")

    choice = resolve_chat_provider(allow_cloud=True)

    assert isinstance(choice.provider, OllamaChatProvider)
    assert choice.downgraded is False


def test_a_local_deployment_never_reports_a_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise every answer on a local-only install carries a warning about a
    provider that was never going to be used."""
    _configure(monkeypatch, "ollama")

    assert resolve_chat_provider(allow_cloud=False).note is None


# --- what the user is told --------------------------------------------------


def test_a_downgrade_is_explained_rather_than_silent() -> None:
    """A worse answer for an invisible reason teaches the wrong lesson.

    The user concludes the system is bad at this, when in fact it was denied
    the model that would have answered well.
    """
    from app.llm.providers import ProviderChoice

    note = ProviderChoice(provider=OllamaChatProvider(), downgraded=True).note

    assert note is not None
    assert "not been opted in" in note
    # It must say what enabling it would mean, not just offer the switch.
    assert "sent to the provider" in note


def test_no_note_when_nothing_was_withheld() -> None:
    from app.llm.providers import ProviderChoice

    assert ProviderChoice(provider=OllamaChatProvider()).note is None


# --- the classification itself ----------------------------------------------


def test_ollama_is_not_treated_as_a_remote_provider() -> None:
    """It runs on the operator's own hardware; nothing leaves the machine."""
    assert "ollama" not in REMOTE_PROVIDERS


def test_gemini_is_treated_as_remote() -> None:
    assert "gemini" in REMOTE_PROVIDERS


def test_every_configurable_provider_is_classified() -> None:
    """A new provider must be a deliberate privacy decision.

    If someone adds a third provider to the config literal and forgets this
    set, it would default to "local" and silently bypass consent. This fails
    the moment that happens.
    """
    import typing

    from app.core.config import Settings

    configurable = set(typing.get_args(Settings.model_fields["llm_provider"].annotation))
    known_local = {"ollama"}

    assert configurable == known_local | REMOTE_PROVIDERS, (
        "a provider was added without deciding whether it is remote; "
        "unclassified providers would bypass per-repository consent"
    )
