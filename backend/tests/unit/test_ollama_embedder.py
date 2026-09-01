"""The Ollama embedding client.

The failures worth testing are the quiet ones. A wrong vector count or width
would not raise on its own -- it would pair vectors with the wrong chunks and
surface months later as bad retrieval, so both are checked on every response.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import ExternalServiceError
from app.llm.ollama import OllamaEmbedder

Handler = Callable[[httpx.Request], httpx.Response]
InstallMock = Callable[[Handler], list[httpx.Request]]

DIMENSIONS = 768


@pytest.fixture
def mock_ollama(monkeypatch: pytest.MonkeyPatch) -> InstallMock:
    original = httpx.Client

    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        def recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def factory(**kwargs: object) -> httpx.Client:
            kwargs.pop("timeout", None)
            return original(transport=httpx.MockTransport(recording))

        monkeypatch.setattr(httpx, "Client", factory)
        return seen

    return install


def _vectors(count: int, width: int = DIMENSIONS) -> Handler:
    payload = {"embeddings": [[0.01] * width for _ in range(count)]}
    return lambda _request: httpx.Response(200, json=payload)


def test_embed_returns_one_vector_per_input(mock_ollama: InstallMock) -> None:
    requests = mock_ollama(_vectors(3))

    vectors = OllamaEmbedder().embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(v) == DIMENSIONS for v in vectors)
    # One request for the whole batch, not one per input.
    assert len(requests) == 1


def test_the_whole_batch_is_sent_in_one_request(mock_ollama: InstallMock) -> None:
    import json

    requests = mock_ollama(_vectors(4))
    OllamaEmbedder().embed(["w", "x", "y", "z"])

    body = json.loads(requests[0].content)
    assert body["input"] == ["w", "x", "y", "z"]
    assert body["model"] == get_settings().embedding_model


def test_an_empty_batch_makes_no_request(mock_ollama: InstallMock) -> None:
    requests = mock_ollama(_vectors(0))

    assert OllamaEmbedder().embed([]) == []
    assert requests == []


def test_a_short_response_is_rejected(mock_ollama: InstallMock) -> None:
    """Two vectors for three inputs would misalign every chunk after it."""
    mock_ollama(_vectors(2))

    with pytest.raises(ExternalServiceError, match="2 vectors for 3 inputs"):
        OllamaEmbedder().embed(["a", "b", "c"])


def test_wrong_dimensions_are_rejected_with_an_actionable_message(
    mock_ollama: InstallMock,
) -> None:
    """A model swapped for one of another width must fail, not truncate."""
    mock_ollama(_vectors(1, width=384))

    with pytest.raises(ExternalServiceError) as raised:
        OllamaEmbedder().embed(["a"])

    message = str(raised.value)
    assert "384" in message
    assert str(DIMENSIONS) in message
    assert "re-index" in message.lower()


def test_a_missing_model_says_how_to_fix_it(mock_ollama: InstallMock) -> None:
    mock_ollama(lambda _r: httpx.Response(404, json={"error": "model not found"}))

    with pytest.raises(ExternalServiceError, match="ollama pull"):
        OllamaEmbedder().embed(["a"])


def test_an_unreachable_server_is_an_upstream_error(mock_ollama: InstallMock) -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    mock_ollama(refused)

    with pytest.raises(ExternalServiceError):
        OllamaEmbedder().embed(["a"])


def test_a_malformed_response_is_an_upstream_error(mock_ollama: InstallMock) -> None:
    mock_ollama(lambda _r: httpx.Response(200, json={"unexpected": "shape"}))

    with pytest.raises(ExternalServiceError, match="no vectors"):
        OllamaEmbedder().embed(["a"])


def test_availability_check_accepts_a_tag_qualified_model(
    mock_ollama: InstallMock,
) -> None:
    """Ollama reports "nomic-embed-text:latest" for "nomic-embed-text"."""
    model = get_settings().embedding_model
    mock_ollama(
        lambda _r: httpx.Response(200, json={"models": [{"name": f"{model}:latest"}]})
    )

    OllamaEmbedder().check_available()


def test_availability_check_reports_a_model_that_is_not_pulled(
    mock_ollama: InstallMock,
) -> None:
    mock_ollama(lambda _r: httpx.Response(200, json={"models": [{"name": "llama3:latest"}]}))

    with pytest.raises(ExternalServiceError, match="ollama pull"):
        OllamaEmbedder().check_available()


def test_availability_check_reports_a_stopped_server(mock_ollama: InstallMock) -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    mock_ollama(refused)

    with pytest.raises(ExternalServiceError, match="Is Ollama running"):
        OllamaEmbedder().check_available()
