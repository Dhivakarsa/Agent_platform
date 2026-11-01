"""
Unit tests for the two mandatory pinned meta-tools:
1. RAG Pipeline Tool
2. System Search Tool
"""

import pytest
from app.db.session import init_db
from app.meta_tools.rag_pipeline_tool import RAGPipelineTool
from app.meta_tools.system_search_tool import SystemSearchTool
from app.services.ingestion import PostmanCollectionIngester, generate_full_paypal_collection
from app.services.tool_registry import ToolRegistry


@pytest.mark.asyncio
async def test_rag_pipeline_tool():
    rag = RAGPipelineTool()
    res = await rag.query("What are the valid refund reason codes for PayPal?")

    assert res["match_count"] > 0
    assert any("refund" in str(r).lower() or "reason" in str(r).lower() for r in res["results"])


@pytest.mark.asyncio
async def test_system_search_capabilities():
    await init_db()
    registry = ToolRegistry()
    tools = PostmanCollectionIngester(collection_data=generate_full_paypal_collection()).parse_collection()
    await registry.register_batch(tools)

    sys_tool = SystemSearchTool(registry=registry)
    res = await sys_tool.query(scope="capabilities", query="invoicing")

    assert res["scope"] == "capabilities"
    assert len(res["matched_tools"]) > 0
    assert any("invoice" in t["name"] for t in res["matched_tools"])


@pytest.mark.asyncio
async def test_system_search_execution_logs():
    await init_db()
    sys_tool = SystemSearchTool()
    res = await sys_tool.query(scope="execution_logs", query="")

    assert res["scope"] == "execution_logs"
    assert "logs" in res

