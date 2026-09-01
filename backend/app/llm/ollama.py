"""Ollama-backed embeddings.

Uses ``POST /api/embed``, which accepts a batch and returns one vector per
input. Batching matters: measured against ``nomic-embed-text``, 32 chunks in
one request cost ~109ms each, against ~700ms each one at a time.

Every failure mode here is treated as loud rather than lenient. A wrong number
of vectors, or vectors of the wrong width, would corrupt the index in a way
that surfaces months later as bad retrieval rather than as an error, so both
are checked on every response.
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

_EMBED_PATH: Final = "/api/embed"
_TAGS_PATH: Final = "/api/tags"


class OllamaEmbedder:
    """Embeds text with a locally running Ollama model."""

    def __init__(self, settings: Settings | None = None) -> None:
        resolved = settings or get_settings()
        self._base_url = resolved.ollama_base_url.rstrip("/")
        self._model = resolved.embedding_model
        self._dimensions = resolved.embedding_dimensions
        # The first request after a cold start loads the model into memory,
        # which can take tens of seconds. The timeout has to allow for that or
        # the very first index of the day fails.
        self._timeout = httpx.Timeout(float(resolved.llm_timeout_seconds), connect=10.0)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, preserving order."""
        if not texts:
            return []

        payload = {"model": self._model, "input": texts}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(f"{self._base_url}{_EMBED_PATH}", json=payload)
        except httpx.HTTPError as exc:
            logger.warning("ollama_request_failed", error=type(exc).__name__)
            raise ExternalServiceError(
                f"The embedding model at {self._base_url} could not be reached"
            ) from exc

        if response.status_code == 404:
            # Ollama answers 404 when the model has never been pulled. That is
            # a setup problem with an exact fix, so it is worth saying plainly
            # rather than reporting as a generic upstream failure.
            raise ExternalServiceError(
                f"Ollama has no model named '{self._model}'. "
                f"Pull it first: ollama pull {self._model}"
            )
        if response.status_code >= 400:
            logger.warning("ollama_error_response", status=response.status_code)
            raise ExternalServiceError(f"The embedding model returned {response.status_code}")

        return self._parse(response.json(), expected=len(texts))

    def _parse(self, body: dict[str, Any], *, expected: int) -> list[list[float]]:
        vectors = body.get("embeddings")
        if not isinstance(vectors, list):
            raise ExternalServiceError("The embedding model returned no vectors")

        if len(vectors) != expected:
            # Silently accepting a short batch would pair vectors with the
            # wrong chunks from that point on.
            raise ExternalServiceError(
                f"The embedding model returned {len(vectors)} vectors for {expected} inputs"
            )

        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                width = len(vector) if isinstance(vector, list) else "?"
                raise ExternalServiceError(
                    f"Model '{self._model}' produced {width}-dimensional vectors, but "
                    f"EMBEDDING_DIMENSIONS is {self._dimensions}. These must match the "
                    f"database column; re-index after correcting the setting."
                )

        return [[float(value) for value in vector] for vector in vectors]

    def check_available(self) -> None:
        """Confirm Ollama is reachable and the configured model is present.

        Called before a long indexing run so a missing model fails in seconds
        with an actionable message, rather than after minutes of parsing.
        """
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
                response = client.get(f"{self._base_url}{_TAGS_PATH}")
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                f"The embedding model at {self._base_url} could not be reached. "
                "Is Ollama running?"
            ) from exc

        if response.status_code >= 400:
            raise ExternalServiceError(f"Ollama returned {response.status_code}")

        models: list[dict[str, Any]] = response.json().get("models", [])
        # Ollama reports "nomic-embed-text:latest" for a model pulled as
        # "nomic-embed-text", so an exact match would wrongly report it absent.
        names = {str(entry.get("name", "")) for entry in models}
        if not any(name == self._model or name.startswith(f"{self._model}:") for name in names):
            raise ExternalServiceError(
                f"Ollama has no model named '{self._model}'. "
                f"Pull it first: ollama pull {self._model}"
            )
