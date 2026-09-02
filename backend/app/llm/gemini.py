"""Chat completions from Google's Gemini API.

The second `ChatProvider`, alongside Ollama. It exists because the local models
that fit on the development machine are the ceiling on milestones 7 and 9, not
the retrieval or the agent machinery around them (docs/README.md). ADR-013
recorded the options; this is the provider-interface option it recommended.

**The API key never leaves this module in a readable form.** It is held as a
`SecretStr`, sent as the `x-goog-api-key` header, and never placed in a URL.
That distinction is the whole point: query strings are recorded by proxies, by
server access logs, and -- the reason it matters here -- by `httpx`'s own
exception messages, which quote the request URL. A key in the URL would end up
in this project's structured logs the first time a request timed out.

Nothing here logs the key, the prompt, or the answer. The prompt can contain
repository content, which is the user's code.
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

# Gemini has no system role. The system prompt goes in system_instruction,
# which is a separate field rather than a first message -- putting it in the
# conversation would let it be treated as ordinary user text.
_GENERATE: Final = "generateContent"

# Retried: the API returns these transiently, and a benchmark that drops a
# question on a 503 reports a number for a different set of questions than the
# one it claims to have measured. Observed directly -- two of three probe
# questions failed this way on one run.
_RETRYABLE: Final = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS: Final = 4
_BACKOFF_SECONDS: Final = 2.0
# A 429 on the free tier is a per-minute window, so a two-second backoff retries
# inside the same window and fails again. Wait long enough to leave it.
_RATE_LIMIT_WAIT: Final = 20.0
_MAX_RETRY_WAIT: Final = 60.0


class GeminiChatProvider:
    """Chat completions from the Gemini API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._settings.gemini_model

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> ChatCompletion:
        settings = self._settings
        resolved = model or settings.gemini_model

        key = settings.gemini_api_key.get_secret_value()
        if not key:
            raise ExternalServiceError(
                "LLM_PROVIDER is 'gemini' but GEMINI_API_KEY is not set."
            )

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature},
        }

        url = f"{settings.gemini_base_url.rstrip('/')}/models/{resolved}:{_GENERATE}"
        timeout = httpx.Timeout(float(settings.llm_timeout_seconds), connect=10.0)
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        started = time.perf_counter()

        response: httpx.Response | None = None
        last_error: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                # Only the exception's type. Its string form quotes the request,
                # and while the key is in a header rather than the URL, logging
                # the message is a habit that would leak one the day it moves.
                last_error = exc
                logger.warning(
                    "gemini_request_failed",
                    error=type(exc).__name__,
                    attempt=attempt,
                )
                response = None
            else:
                if response.status_code not in _RETRYABLE:
                    break
                if _is_daily_quota_exhausted(response):
                    # Retrying spends the very budget it is waiting for: every
                    # attempt is a billed request, so a four-attempt policy
                    # burns a 20-per-day allowance five times faster and still
                    # fails. A per-minute limit clears; a per-day one does not.
                    logger.warning("gemini_daily_quota_exhausted", attempt=attempt)
                    break
                logger.warning(
                    "gemini_transient_status",
                    status=response.status_code,
                    attempt=attempt,
                )

            if attempt < _MAX_ATTEMPTS:
                time.sleep(_retry_delay(response, attempt))

        if response is None:
            raise ExternalServiceError(
                "The Gemini API could not be reached"
            ) from last_error

        duration_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 400:
            raise ExternalServiceError(
                "Gemini rejected the request. If this persists the API key may "
                "be malformed."
            )
        if response.status_code in (401, 403):
            raise ExternalServiceError(
                "Gemini rejected the API key. Check GEMINI_API_KEY."
            )
        if response.status_code == 404:
            raise ExternalServiceError(
                f"Gemini has no model named '{resolved}', or it is not available "
                "to this key."
            )
        if response.status_code == 429:
            if _is_daily_quota_exhausted(response):
                raise ExternalServiceError(
                    "Gemini's daily free-tier request quota for this model is "
                    "exhausted. It resets on Google's schedule; another model "
                    "has its own separate allowance. Not a bug, and not "
                    "something retrying can fix."
                )
            raise ExternalServiceError(
                "Gemini rate limit exceeded after retries. This is a quota "
                "question rather than a bug."
            )
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"The Gemini API returned {response.status_code}"
            )

        return _parse(response.json(), model=resolved, duration_ms=duration_ms)


def _is_daily_quota_exhausted(response: httpx.Response) -> bool:
    """Whether a 429 is a daily allowance rather than a burst limit.

    The distinction decides whether retrying is useful or actively harmful.
    Google reports it in the error details as a ``quotaId`` -- the free tier's
    is ``GenerateRequestsPerDayPerProjectPerModel-FreeTier`` -- so the check is
    for a per-day quota rather than for any particular tier's name.

    Anything unparseable is treated as *not* a daily limit, so an unexpected
    body shape costs a few retries rather than silently disabling them.
    """
    if response.status_code != 429:
        return False
    try:
        details = (response.json().get("error") or {}).get("details") or []
    except ValueError:
        return False

    for detail in details:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            if "perday" in str(violation.get("quotaId", "")).lower():
                return True
    return False


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """How long to wait before the next attempt.

    A rate limit is not a transient blip and exponential backoff from two
    seconds does not clear one: the free tier's window is per minute, so three
    quick retries all land inside the same window and all fail. When the API
    says how long to wait, that is the answer; otherwise a 429 waits long
    enough to leave the window it was refused in.

    Capped, because a benchmark that stalls for minutes on a quota problem
    should report the quota problem rather than appear to hang.
    """
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), _MAX_RETRY_WAIT)
            except ValueError:
                pass
        if response.status_code == 429:
            return min(_RATE_LIMIT_WAIT * attempt, _MAX_RETRY_WAIT)
    return _BACKOFF_SECONDS * attempt


def _parse(body: dict[str, Any], *, model: str, duration_ms: int) -> ChatCompletion:
    """Turn a generateContent response into a ChatCompletion.

    Written defensively on purpose. A blocked or truncated response still
    returns 200 with a candidate that has no text, and treating that as an
    empty answer would be indistinguishable from the model having nothing to
    say. The finish reason is the difference, so it is surfaced.
    """
    candidates = body.get("candidates") or []
    if not candidates:
        blocked = (body.get("promptFeedback") or {}).get("blockReason")
        raise ExternalServiceError(
            f"Gemini returned no answer (blocked: {blocked})"
            if blocked
            else "Gemini returned no answer"
        )

    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()

    if not text:
        reason = candidate.get("finishReason") or "unknown"
        if reason == "MAX_TOKENS":
            raise ExternalServiceError(
                "Gemini hit its output limit before producing any text"
            )
        raise ExternalServiceError(
            f"Gemini returned an empty answer (finish reason: {reason})"
        )

    usage = body.get("usageMetadata") or {}
    return ChatCompletion(
        answer=text,
        model=model,
        duration_ms=duration_ms,
        prompt_tokens=usage.get("promptTokenCount"),
        completion_tokens=usage.get("candidatesTokenCount"),
    )
