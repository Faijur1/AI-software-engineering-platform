"""Chat completions from a local Ollama.

Lifted out of ``chat.py`` unchanged in behaviour when a second provider
arrived. It stays the default: it needs no key, no network beyond localhost,
and no per-token cost, so a checkout with an empty ``.env`` still runs the whole
system end to end.
"""

from __future__ import annotations

import time
from typing import Any, Final

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ExternalServiceError
from app.core.logging import get_logger
from app.llm.types import ChatCompletion

logger = get_logger(__name__)

_CHAT_PATH: Final = "/api/chat"


class OllamaChatProvider:
    """Chat completions from a local Ollama server."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> ChatCompletion:
        settings = self._settings
        resolved_model = model or settings.llm_model

        payload: dict[str, Any] = {
            "model": resolved_model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        timeout = httpx.Timeout(float(settings.llm_timeout_seconds), connect=10.0)
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{settings.ollama_base_url.rstrip('/')}{_CHAT_PATH}", json=payload
                )
        except httpx.HTTPError as exc:
            logger.warning("chat_request_failed", error=type(exc).__name__)
            raise ExternalServiceError(
                f"The language model at {settings.ollama_base_url} could not be reached"
            ) from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 404:
            raise ExternalServiceError(
                f"Ollama has no model named '{resolved_model}'. "
                f"Pull it first: ollama pull {resolved_model}"
            )
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"The language model returned {response.status_code}"
            )

        body: dict[str, Any] = response.json()
        content = body.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ExternalServiceError("The language model returned an empty answer")

        # Ollama reports nanoseconds, and reports 0 when it served from cache;
        # the measured elapsed time is the honest fallback.
        reported_ms = int(body.get("total_duration", 0) / 1_000_000)

        return ChatCompletion(
            answer=content.strip(),
            model=resolved_model,
            duration_ms=reported_ms or elapsed_ms,
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
        )
