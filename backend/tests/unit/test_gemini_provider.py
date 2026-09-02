"""The Gemini chat provider.

Two things are pinned here. The response handling, because a 200 from this API
does not mean there is an answer in the body. And the handling of the key,
because a leaked credential is not a bug that shows up in a test run -- it shows
up in a log aggregator months later.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ExternalServiceError
from app.llm import gemini
from app.llm.gemini import GeminiChatProvider

KEY = "test-gemini-key-not-a-real-one"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": KEY,
        "gemini_model": "gemini-3.6-flash",
        "llm_timeout_seconds": 30,
    }
    values.update(overrides)
    return Settings(**values)


def _ok_body(text: str = "hello [1]") -> dict[str, Any]:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4},
    }


# --- the key ----------------------------------------------------------------


def test_the_key_is_sent_as_a_header_and_never_in_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key in a query string reaches proxy logs and exception text.

    httpx quotes the request URL in its own error messages, so a key placed
    there would land in this project's structured logs the first time a request
    timed out. The header is the whole reason this is asserted.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key")
        return httpx.Response(200, json=_ok_body())

    _install(monkeypatch, handler)
    GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert seen["header"] == KEY
    assert KEY not in seen["url"]
    assert "key=" not in seen["url"]


def test_an_unreachable_api_does_not_put_the_key_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transport error is retried, so the backoff is stubbed out.
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    _install(monkeypatch, handler)

    with pytest.raises(ExternalServiceError) as raised:
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert KEY not in str(raised.value)


def test_a_rejected_key_is_reported_without_quoting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        lambda request: httpx.Response(403, json={"error": {"message": "bad key"}}),
    )

    with pytest.raises(ExternalServiceError) as raised:
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert KEY not in str(raised.value)
    assert "GEMINI_API_KEY" in str(raised.value)


def test_a_missing_key_is_a_configuration_error_not_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail before the call rather than sending an unauthenticated request."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_ok_body())

    _install(monkeypatch, handler)

    with pytest.raises(ExternalServiceError, match="GEMINI_API_KEY"):
        GeminiChatProvider(_settings(gemini_api_key="")).complete(system="s", user="u")

    assert not called


# --- responses --------------------------------------------------------------


def test_a_normal_answer_is_returned_with_its_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda request: httpx.Response(200, json=_ok_body()))

    completion = GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert completion.answer == "hello [1]"
    assert completion.model == "gemini-3.6-flash"
    assert completion.prompt_tokens == 11
    assert completion.completion_tokens == 4


def test_the_system_prompt_goes_in_system_instruction_not_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini has no system role.

    Putting the instructions in `contents` would make them ordinary user text,
    sitting alongside repository content the prompt explicitly labels as data.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_ok_body())

    _install(monkeypatch, handler)
    GeminiChatProvider(_settings()).complete(system="RULES", user="question")

    assert seen["systemInstruction"]["parts"][0]["text"] == "RULES"
    assert seen["contents"][0]["parts"][0]["text"] == "question"
    assert "RULES" not in str(seen["contents"])


def test_a_blocked_prompt_is_not_reported_as_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 with no candidates. Silence and refusal are different outcomes."""
    _install(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"promptFeedback": {"blockReason": "SAFETY"}}
        ),
    )

    with pytest.raises(ExternalServiceError, match="SAFETY"):
        GeminiChatProvider(_settings()).complete(system="s", user="u")


def test_hitting_the_output_limit_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncation before any text is a 200 with an empty part list."""
    _install(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]},
        ),
    )

    with pytest.raises(ExternalServiceError, match="output limit"):
        GeminiChatProvider(_settings()).complete(system="s", user="u")


def test_a_quota_error_is_named_as_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 is a billing question, and saying so saves a debugging session.

    Retried first -- a burst limit does clear -- so the backoff is stubbed out
    to keep the suite fast.
    """
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)
    _install(monkeypatch, lambda request: httpx.Response(429, json={}))

    with pytest.raises(ExternalServiceError, match="quota"):
        GeminiChatProvider(_settings()).complete(system="s", user="u")


def test_an_unknown_model_names_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(404, json={}))

    with pytest.raises(ExternalServiceError, match=r"gemini-3\.6-flash"):
        GeminiChatProvider(_settings()).complete(system="s", user="u")


def _install(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Point httpx.Client at a mock transport for the duration of a test."""
    original = httpx.Client

    def factory(**kwargs: Any) -> httpx.Client:
        kwargs.pop("timeout", None)
        return original(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", factory)


# --- transient failures -----------------------------------------------------


def test_a_transient_503_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed for real: two of three probe questions failed on one run.

    A benchmark that drops a question on a 503 reports a number for a different
    set of questions than the one it claims to have measured.
    """
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json=_ok_body())

    _install(monkeypatch, handler)

    completion = GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert attempts == 2
    assert completion.answer == "hello [1]"


def test_retries_are_bounded_and_the_last_status_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={})

    _install(monkeypatch, handler)

    with pytest.raises(ExternalServiceError, match="503"):
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    # Against the constant, not a copy of its value: the point is that retrying
    # stops, not that it stops at any particular number.
    assert attempts == gemini._MAX_ATTEMPTS


def test_a_rejected_key_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a 403 wastes time on an outcome that cannot change."""
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(403, json={})

    _install(monkeypatch, handler)

    with pytest.raises(ExternalServiceError):
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert attempts == 1


def test_a_rate_limit_waits_long_enough_to_leave_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exponential backoff from two seconds does not clear a per-minute quota.

    Three quick retries all land inside the window that just refused them, and
    the run fails having waited six seconds for nothing.
    """
    slept: list[float] = []
    monkeypatch.setattr("app.llm.gemini.time.sleep", slept.append)
    _install(monkeypatch, lambda request: httpx.Response(429, json={}))

    with pytest.raises(ExternalServiceError):
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert slept and min(slept) >= 20.0


def test_retry_after_is_obeyed_when_the_api_sends_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server knows better than any guess here."""
    slept: list[float] = []
    monkeypatch.setattr("app.llm.gemini.time.sleep", slept.append)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={}, headers={"retry-after": "7"})
        return httpx.Response(200, json=_ok_body())

    _install(monkeypatch, handler)
    GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert slept == [7.0]


def test_the_wait_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A benchmark should report a quota problem, not appear to hang."""
    slept: list[float] = []
    monkeypatch.setattr("app.llm.gemini.time.sleep", slept.append)
    _install(
        monkeypatch,
        lambda request: httpx.Response(429, json={}, headers={"retry-after": "3600"}),
    )

    with pytest.raises(ExternalServiceError):
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert max(slept) <= 60.0


def test_a_daily_quota_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a daily quota spends the budget it is waiting for.

    Every attempt is a billed request, so a four-attempt policy burns a
    20-per-day allowance five times faster and still fails. Observed for real:
    the retries alone exhausted a second model's allowance.
    """
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            json={
                "error": {
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "quotaId": (
                                        "GenerateRequestsPerDayPerProjectPerModel"
                                        "-FreeTier"
                                    )
                                }
                            ],
                        }
                    ]
                }
            },
        )

    _install(monkeypatch, handler)

    with pytest.raises(ExternalServiceError, match="daily"):
        GeminiChatProvider(_settings()).complete(system="s", user="u")

    assert attempts == 1


def test_a_per_minute_limit_is_still_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """The distinction is per-day versus burst, not 429 versus everything."""
    monkeypatch.setattr("app.llm.gemini.time.sleep", lambda _s: None)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                "violations": [
                                    {"quotaId": "GenerateRequestsPerMinute-FreeTier"}
                                ],
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, json=_ok_body())

    _install(monkeypatch, handler)

    assert GeminiChatProvider(_settings()).complete(system="s", user="u").answer
    assert attempts == 2
