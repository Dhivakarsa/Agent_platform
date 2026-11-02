"""
Single embedding provider for every collection in the platform.

Both the tool index and the knowledge base must embed with the same provider that will
later embed the query, otherwise the vectors live in different spaces and retrieval
returns noise. Centralising that choice here is what keeps the two consistent, and means
configuring an API key improves document retrieval as well as tool retrieval.

Falls back to a deterministic local embedding when no key is configured, so indexing and
search still work offline.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import get_settings
from app.db.vector_store import generate_deterministic_embedding

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Embeds text via Gemini when configured, otherwise with a local hash embedding."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.dimension = self.settings.embedding_dimension
        self._cache: dict[str, list[float]] = {}
        self._remote_disabled = False
        self._client = None

    # ── Provider ─────────────────────────────────────────────────────────

    @property
    def provider(self) -> str:
        """Which provider embeddings are currently produced by."""
        if self.settings.GEMINI_API_KEY and not self._remote_disabled:
            return "gemini"
        return "local"

    def _remote_client(self):
        """Lazily build the GenAI client; returns None when unavailable."""
        if self._client is not None:
            return self._client
        if self._remote_disabled or not self.settings.GEMINI_API_KEY:
            return None
        try:
            from google import genai

            self._client = genai.Client(api_key=self.settings.GEMINI_API_KEY)
            logger.info("Embedding provider: Gemini (%s)", self.settings.embedding_model)
            return self._client
        except Exception as exc:
            self._remote_disabled = True
            logger.info("Gemini embeddings unavailable (%s); using local embeddings.", exc)
            return None

    # ── Embedding ────────────────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        """Return the embedding for one piece of text, cached per process."""
        if text in self._cache:
            return self._cache[text]

        vector: list[float] = []
        client = self._remote_client()
        if client is not None:
            try:
                response = client.models.embed_content(
                    model=self.settings.embedding_model,
                    contents=text,
                )
                embeddings = getattr(response, "embeddings", None)
                if embeddings:
                    vector = list(embeddings[0].values)
                else:
                    single = getattr(response, "embedding", None)
                    if single is not None and getattr(single, "values", None):
                        vector = list(single.values)
            except Exception as exc:
                # One failure disables remote embedding for the process rather than
                # retrying on every chunk of a large document.
                self._remote_disabled = True
                self._client = None
                logger.warning(
                    "Gemini embedding failed (%s); falling back to local embeddings. "
                    "Content indexed from here on will not match previously indexed "
                    "vectors until the collections are rebuilt.",
                    exc,
                )

        if not vector:
            vector = generate_deterministic_embedding(text, self.dimension)

        self._cache[text] = vector
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed several pieces of text, for example the chunks of one document."""
        return [self.embed(t) for t in texts]


_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
