"""
Qdrant vector store client and helper utilities.

Provides an async/sync wrapper around QdrantClient with in-memory fallback
and cosine similarity search with payload filtering.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
import numpy as np

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest_models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import get_settings

logger = logging.getLogger(__name__)


class VectorStoreService:
    """Manages Qdrant vector collections for tool discovery and RAG documentation."""

    def __init__(self, use_memory: Optional[bool] = None) -> None:
        settings = get_settings()
        self.use_memory = use_memory if use_memory is not None else settings.qdrant_use_memory
        self.dim = settings.embedding_dimension

        if self.use_memory:
            logger.info("Initializing in-memory Qdrant client")
            self.client = QdrantClient(":memory:")
        else:
            logger.info("Connecting to Qdrant at %s:%s", settings.qdrant_host, settings.qdrant_port)
            self.client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

        self.tool_collection = settings.qdrant_collection_name
        self.knowledge_collection = settings.qdrant_knowledge_collection
        self._init_collections()

    def _init_collections(self) -> None:
        """Create collections with cosine distance metric if they do not exist."""
        for collection_name in [self.tool_collection, self.knowledge_collection]:
            try:
                collections = self.client.get_collections().collections
                exists = any(c.name == collection_name for c in collections)
                if not exists:
                    self.client.create_collection(
                        collection_name=collection_name,
                        vectors_config=rest_models.VectorParams(
                            size=self.dim,
                            distance=rest_models.Distance.COSINE,
                        ),
                    )
                    logger.info("Created Qdrant collection: %s (dim=%d)", collection_name, self.dim)
            except Exception as exc:
                logger.warning("Could not verify/create collection %s: %s", collection_name, exc)

    def _normalize_vector(self, v: list[float]) -> list[float]:
        """Ensure vector dimension strictly matches Qdrant collection size."""
        if len(v) == self.dim:
            return v
        if len(v) > self.dim:
            return v[:self.dim]
        return v + [0.0] * (self.dim - len(v))

    def upsert_vectors(
        self,
        collection_name: str,
        points: list[dict[str, Any]],
    ) -> None:
        """
        Upsert points to a collection.
        Each point dict should contain: 'id' (int or uuid), 'vector' (list[float]), and 'payload' (dict).
        """
        qdrant_points = [
            rest_models.PointStruct(
                id=p["id"],
                vector=self._normalize_vector(p["vector"]),
                payload=p.get("payload", {}),
            )
            for p in points
        ]
        self.client.upsert(
            collection_name=collection_name,
            points=qdrant_points,
        )

    def search_vectors(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 6,
        domain_filter: Optional[str] = None,
    ) -> list[rest_models.ScoredPoint]:
        """
        Perform dense vector search with optional payload domain filter.
        """
        query_filter = None
        if domain_filter:
            query_filter = rest_models.Filter(
                must=[
                    rest_models.FieldCondition(
                        key="domain",
                        match=rest_models.MatchValue(value=domain_filter),
                    )
                ]
            )

        norm_query = self._normalize_vector(query_vector)
        if hasattr(self.client, "query_points"):
            res = self.client.query_points(
                collection_name=collection_name,
                query=norm_query,
                query_filter=query_filter,
                limit=limit,
            )
            return res.points
        elif hasattr(self.client, "search"):
            return self.client.search(
                collection_name=collection_name,
                query_vector=norm_query,
                query_filter=query_filter,
                limit=limit,
            )
        return []

    def count(self, collection_name: str) -> int:
        """Return point count in collection."""
        try:
            return self.client.count(collection_name=collection_name).count
        except Exception:
            return 0


# Deterministic local embedding generator when external API is not reachable
def generate_deterministic_embedding(text: str, dimension: int = 768) -> list[float]:
    """
    Generate a normalized deterministic pseudo-embedding based on token hashes.
    Ensures hermetic execution, offline benchmarking, and zero-external-dependency tests.
    """
    import hashlib
    words = text.lower().replace("_", " ").split()
    vector = np.zeros(dimension, dtype=np.float32)

    for i, word in enumerate(words):
        # Create multiple hash projections per word
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        idx = h % dimension
        weight = 1.0 / (1.0 + (i * 0.1))
        vector[idx] += weight

        # Secondary hash for cross-feature density
        h2 = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
        idx2 = h2 % dimension
        vector[idx2] += weight * 0.5

    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector.tolist()


_vector_store_instance: Optional[VectorStoreService] = None


def get_vector_store() -> VectorStoreService:
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = VectorStoreService()
    return _vector_store_instance

