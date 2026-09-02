"""The real embedding model.

Everything else mocks the provider, so nothing else would notice if Ollama
changed its response shape, its vector width, or its batching behaviour. These
tests talk to the actual model, and are skipped when it is unavailable rather
than failing a suite run on a machine without it.

    pytest -m llm
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.errors import ExternalServiceError
from app.llm.ollama import OllamaEmbedder

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def embedder() -> OllamaEmbedder:
    provider = OllamaEmbedder()
    try:
        provider.check_available()
    except ExternalServiceError as exc:
        pytest.skip(f"Ollama unavailable: {exc.message}")
    return provider


def test_the_model_produces_vectors_of_the_configured_width(
    embedder: OllamaEmbedder,
) -> None:
    """The one assumption the database schema depends on."""
    vectors = embedder.embed(["def add(a, b):\n    return a + b\n"])

    assert len(vectors) == 1
    assert len(vectors[0]) == get_settings().embedding_dimensions


def test_a_batch_returns_vectors_in_input_order(embedder: OllamaEmbedder) -> None:
    """Order matters: vectors are zipped back onto their chunks positionally."""
    first = "def parse_config(path):\n    return read(path)\n"
    second = "class HttpClient:\n    def get(self, url): ...\n"

    batch = embedder.embed([first, second])
    singles = [embedder.embed([first])[0], embedder.embed([second])[0]]

    assert len(batch) == 2
    for from_batch, alone in zip(batch, singles, strict=True):
        # Same text, same position -- allowing for small numeric drift.
        assert max(abs(a - b) for a, b in zip(from_batch, alone, strict=True)) < 1e-4


def test_similar_code_embeds_closer_than_unrelated_code(
    embedder: OllamaEmbedder,
) -> None:
    """A sanity check on the model, not a retrieval-quality claim.

    Retrieval quality is measured against a labelled benchmark in milestone 6.
    This only confirms the vectors carry real semantic signal, so a
    misconfigured or wrong model would be noticed here rather than much later.
    """
    query = "how do I read a configuration file"
    related = "def load_config(path):\n    with open(path) as f:\n        return json.load(f)\n"
    unrelated = "def rotate_image(img, degrees):\n    return img.rotate(degrees)\n"

    q, near, far = embedder.embed([query, related, unrelated])

    assert _cosine(q, near) > _cosine(q, far)


def test_an_unpulled_model_reports_how_to_fix_it() -> None:
    from app.core.config import Settings

    settings = get_settings().model_copy(update={"embedding_model": "not-a-real-model-xyz"})

    with pytest.raises(ExternalServiceError, match="ollama pull"):
        OllamaEmbedder(settings if isinstance(settings, Settings) else None).check_available()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return float(dot / (norm_a * norm_b))
