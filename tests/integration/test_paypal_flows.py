"""
Integration tests: End-to-end LangGraph agent execution for all assessment scenarios.
"""

import pytest
from app.db.session import init_db
from app.graph.workflow import run_agent
from app.main import seed_system_catalog


@pytest.mark.asyncio
async def test_paypal_invoicing_flow():
    await init_db()
    await seed_system_catalog()

    query = "Send an invoice for $50 to client@example.com"
    response = await run_agent(query=query, session_id="test_invoicing_001")

    assert response.conversation_id == "test_invoicing_001"

    # Sending an invoice mutates external state, so the policy gate must pause the run
    # for human confirmation rather than dispatching it.
    assert response.status == "waiting_approval"
    assert response.approval_status == "pending"
    assert response.pending_approval is not None
    assert "invoice" in response.pending_approval["tool_name"]

    # The captured arguments must carry what the user actually supplied.
    args = response.pending_approval["arguments"]
    assert args.get("recipient_email") == "client@example.com" or args.get("to") == "client@example.com"


@pytest.mark.asyncio
async def test_paypal_reporting_flow():
    await init_db()
    await seed_system_catalog()

    query = "What was my total sales volume last month?"
    response = await run_agent(query=query, session_id="test_reporting_001")

    assert "sales volume" in response.response.lower() or "transactions" in response.response.lower()
    assert len(response.tools_used) >= 1


@pytest.mark.asyncio
async def test_paypal_disputes_flow():
    await init_db()
    await seed_system_catalog()

    query = "Is there a dispute open from user_123?"
    response = await run_agent(query=query, session_id="test_disputes_001")

    assert "dispute" in response.response.lower()
    assert len(response.tools_used) >= 1


@pytest.mark.asyncio
async def test_meta_tool_rag_flow():
    await init_db()
    await seed_system_catalog()

    query = "What are the valid refund reason codes for PayPal?"
    response = await run_agent(query=query, session_id="test_rag_001")

    assert "rag_pipeline_tool" in response.tools_used
    assert "refund" in response.response.lower()


@pytest.mark.asyncio
async def test_meta_tool_system_search_flow():
    await init_db()
    await seed_system_catalog()

    query = "What tools are available for managing invoices?"
    response = await run_agent(query=query, session_id="test_sys_001")

    assert "system_search_tool" in response.tools_used
    assert "invoice" in response.response.lower()

