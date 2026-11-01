"""
Unit tests for two-stage hybrid tool retriever and dependency expansion.
"""

import pytest
from app.db.session import init_db
from app.services.ingestion import PostmanCollectionIngester, generate_full_paypal_collection
from app.services.tool_registry import ToolRegistry
from app.services.tool_retriever import ToolRetriever


@pytest.mark.asyncio
async def test_hybrid_tool_retriever():
    await init_db()
    registry = ToolRegistry()
    raw_collection = generate_full_paypal_collection()
    tools = PostmanCollectionIngester(collection_data=raw_collection).parse_collection()
    await registry.register_batch(tools)

    retriever = ToolRetriever(registry=registry, top_k=5)

    # Invoicing query should prioritize invoice tools and auto-expand prerequisites
    discovered = retriever.retrieve("Send an invoice for $50 to client@example.com")
    discovered_names = [t.name for t in discovered]

    assert any("invoice" in name for name in discovered_names)
    assert len(discovered) <= 8


@pytest.mark.asyncio
async def test_domain_filtering():
    registry = ToolRegistry()
    retriever = ToolRetriever(registry=registry)

    assert retriever._classify_domain("Is there an open dispute?") == "disputes"
    assert retriever._classify_domain("What was my sales volume last month?") == "reporting"
    assert retriever._classify_domain("Send an invoice for $50") == "invoicing"

