"""
Unit tests for Postman collection ingestion and synthetic tool creation.
"""

import pytest
from app.services.ingestion import PostmanCollectionIngester, create_synthetic_tools, generate_full_paypal_collection


def test_postman_collection_ingestion():
    raw_collection = generate_full_paypal_collection()
    ingester = PostmanCollectionIngester(collection_data=raw_collection)
    tools = ingester.parse_collection()

    assert len(tools) >= 50
    tool_names = [t.name for t in tools]
    assert "paypal_create_draft_invoice" in tool_names or any("invoice" in n for n in tool_names)
    assert any("order" in n for n in tool_names)
    assert any("dispute" in n for n in tool_names)


def test_synthetic_tools_generator():
    tools = create_synthetic_tools(count=150)
    assert len(tools) == 150
    assert any("stripe" in t.name for t in tools)
    assert any("slack" in t.name for t in tools)
    assert any("jira" in t.name for t in tools)

