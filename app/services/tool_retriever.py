"""
Two-stage hybrid tool retriever.

Executes domain classification, dense vector search via Qdrant, sparse keyword scoring,
reciprocal rank fusion (RRF), and prerequisite dependency expansion.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.config import get_settings
from app.db.vector_store import generate_deterministic_embedding
from app.schemas.tool import RegisteredTool
from app.services.ingestion import DOMAIN_KEYWORDS
from app.services.tool_registry import ToolRegistry, get_tool_registry

logger = logging.getLogger(__name__)


class ToolRetriever:
    """Discovers and ranks candidate tools for a user query."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        top_k: Optional[int] = None,
    ) -> None:
        self.settings = get_settings()
        self.registry = registry or get_tool_registry()
        self.top_k = top_k or self.settings.retrieval_top_k

    def _classify_domain(self, query: str) -> Optional[str]:
        """
        Stage 1: Intent to operational domain classification using intent keyword patterns.
        """
        query_lower = query.lower()
        domain_scores: dict[str, int] = {}

        for domain, keywords in DOMAIN_KEYWORDS.items():
            score = 0
            for kw in keywords:
                if kw in query_lower:
                    score += 2 if len(kw.split()) > 1 else 1
            if score > 0:
                domain_scores[domain] = score

        if domain_scores:
            best_domain = max(domain_scores.items(), key=lambda x: x[1])[0]
            logger.debug("Classified query domain as '%s' with score %d", best_domain, domain_scores[best_domain])
            return best_domain
        return None

    def _dense_search(
        self,
        query: str,
        domain_filter: Optional[str] = None,
        limit: int = 10,
    ) -> list[tuple[RegisteredTool, float]]:
        """Dense vector search using Qdrant client."""
        # Embed query
        query_vector = self.registry._generate_embedding(query)
        scored_points = self.registry.vector_store.search_vectors(
            collection_name=self.settings.qdrant_collection_name,
            query_vector=query_vector,
            limit=limit,
            domain_filter=domain_filter,
        )

        results: list[tuple[RegisteredTool, float]] = []
        for point in scored_points:
            tool_name = point.payload.get("name") if point.payload else None
            if tool_name:
                tool = self.registry.get_tool(tool_name)
                if tool:
                    results.append((tool, float(point.score)))
        return results

    def _sparse_keyword_search(
        self,
        query: str,
        domain_filter: Optional[str] = None,
        limit: int = 10,
    ) -> list[tuple[RegisteredTool, float]]:
        """Keyword matching across tool name, description, and keywords."""
        query_tokens = set(re.findall(r"\w+", query.lower()))
        all_tools = list(self.registry._memory_cache.values())
        if domain_filter:
            all_tools = [t for t in all_tools if t.domain == domain_filter]

        scored: list[tuple[RegisteredTool, float]] = []
        for tool in all_tools:
            tool_tokens = set(re.findall(r"\w+", f"{tool.name} {tool.description} {' '.join(tool.keywords)}".lower()))
            intersection = query_tokens.intersection(tool_tokens)
            if intersection:
                score = len(intersection) / (len(query_tokens) + 1.0)
                scored.append((tool, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def _reciprocal_rank_fusion(
        self,
        dense_results: list[tuple[RegisteredTool, float]],
        sparse_results: list[tuple[RegisteredTool, float]],
        c: int = 60,
    ) -> list[tuple[RegisteredTool, float]]:
        """Combine dense and sparse rankings using Reciprocal Rank Fusion."""
        rrf_scores: dict[str, float] = {}
        tool_map: dict[str, RegisteredTool] = {}

        for rank, (tool, _) in enumerate(dense_results):
            rrf_scores[tool.name] = rrf_scores.get(tool.name, 0.0) + (1.0 / (c + rank + 1))
            tool_map[tool.name] = tool

        for rank, (tool, _) in enumerate(sparse_results):
            rrf_scores[tool.name] = rrf_scores.get(tool.name, 0.0) + (1.0 / (c + rank + 1))
            tool_map[tool.name] = tool

        sorted_tools = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return [(tool_map[name], score) for name, score in sorted_tools]

    def _expand_dependencies(self, tools: list[RegisteredTool]) -> list[RegisteredTool]:
        """
        Dependency Expansion: If send_invoice is selected, inject create_draft_invoice.
        Maintains order with dependencies prioritized first.
        """
        expanded: list[RegisteredTool] = []
        seen = set()

        for tool in tools:
            # Check prerequisite tools
            for prior_name in tool.requires_prior:
                if prior_name not in seen:
                    prior_tool = self.registry.get_tool(prior_name)
                    if prior_tool:
                        seen.add(prior_name)
                        expanded.append(prior_tool)
            if tool.name not in seen:
                seen.add(tool.name)
                expanded.append(tool)

        return expanded

    def retrieve(
        self,
        query: str,
        domain_hint: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> list[RegisteredTool]:
        """
        Execute full two-stage hybrid retrieval:
        1. Classify domain intent.
        2. Perform dense + sparse search with domain filtering.
        3. Reciprocal Rank Fusion.
        4. Dependency graph expansion.
        """
        k = top_k or self.top_k

        # Stage 1: Domain filter determination
        domain = domain_hint or self._classify_domain(query)

        # Stage 2: Hybrid search
        dense = self._dense_search(query, domain_filter=domain, limit=k * 2)
        sparse = self._sparse_keyword_search(query, domain_filter=domain, limit=k * 2)

        # If domain-restricted search returned too few results, relax domain filter
        if len(dense) < 2 and len(sparse) < 2 and domain is not None:
            dense = self._dense_search(query, domain_filter=None, limit=k * 2)
            sparse = self._sparse_keyword_search(query, domain_filter=None, limit=k * 2)

        fused = self._reciprocal_rank_fusion(dense, sparse)
        top_candidates = [t for t, _ in fused[:k]]

        # Fallback if no tools found
        if not top_candidates:
            all_tools = list(self.registry._memory_cache.values())
            top_candidates = all_tools[:k]

        # Stage 3: Dependency expansion
        expanded = self._expand_dependencies(top_candidates)
        return expanded[: k + 2]


_retriever: Optional[ToolRetriever] = None


def get_tool_retriever() -> ToolRetriever:
    global _retriever
    if _retriever is None:
        _retriever = ToolRetriever()
    return _retriever

