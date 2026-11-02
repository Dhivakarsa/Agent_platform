"""
Integration tests for the human-in-the-loop pause and resume.

The policy gate suspends the graph with ``interrupt()``, so approving a run must continue
the original flow (execute, evaluate, remaining plan steps) rather than dispatching the
pending tool on its own.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import AgentRun, ApprovalRequest, ExecutionLog
from app.db.session import async_session_factory, init_db
from app.graph.workflow import _load_conversation_history, resume_agent, run_agent
from app.main import seed_system_catalog


@pytest.fixture
async def catalog():
    await init_db()
    await seed_system_catalog()


@pytest.mark.asyncio
async def test_mutating_action_suspends_and_reports_why(catalog):
    res = await run_agent(
        query="Send an invoice for $50 to client@example.com",
        user_id="u-approve",
        session_id="s-approve-1",
    )

    assert res.status == "waiting_approval"
    assert res.approval_status == "pending"
    assert res.pending_approval is not None
    assert "invoice" in res.pending_approval["tool_name"]
    # The reason must name the actual trigger, not just a risk tier.
    assert res.pending_approval["reasons"]

    # Nothing was dispatched while waiting.
    async with async_session_factory() as s:
        logs = (
            await s.execute(select(ExecutionLog).where(ExecutionLog.request_id == res.run_id))
        ).scalars().all()
    assert logs == []

    # A pending approval row exists for the run.
    async with async_session_factory() as s:
        appr = (
            await s.execute(select(ApprovalRequest).where(ApprovalRequest.run_id == res.run_id))
        ).scalars().all()
    assert len(appr) == 1
    assert appr[0].status == "pending"


@pytest.mark.asyncio
async def test_approve_resumes_the_graph_and_executes(catalog):
    res = await run_agent(
        query="Send an invoice for $50 to client@example.com",
        user_id="u-approve",
        session_id="s-approve-2",
    )
    assert res.status == "waiting_approval"

    resumed = await resume_agent(run_id=res.run_id, action="approve")

    assert resumed is not None
    assert resumed.status == "completed"
    assert resumed.approval_status == "approved"
    assert any("invoice" in t for t in resumed.tools_used)

    # The execution went through the engine, under the same request id, so the audit
    # trail stays attached to the original run.
    async with async_session_factory() as s:
        logs = (
            await s.execute(select(ExecutionLog).where(ExecutionLog.request_id == res.run_id))
        ).scalars().all()
    assert logs, "approved action produced no audit row"
    assert logs[0].user_id == "u-approve"

    # The approval row is settled, not left pending.
    async with async_session_factory() as s:
        appr = (
            await s.execute(select(ApprovalRequest).where(ApprovalRequest.run_id == res.run_id))
        ).scalars().first()
    assert appr.status == "approved"

    # The run record reflects the final outcome.
    async with async_session_factory() as s:
        run = (
            await s.execute(select(AgentRun).where(AgentRun.run_id == res.run_id))
        ).scalar_one()
    assert run.status == "completed"
    assert run.approval_status == "approved"
    assert run.final_response


@pytest.mark.asyncio
async def test_reject_cancels_without_executing(catalog):
    res = await run_agent(
        query="Send an invoice for $50 to client@example.com",
        user_id="u-reject",
        session_id="s-reject-1",
    )
    assert res.status == "waiting_approval"

    resumed = await resume_agent(run_id=res.run_id, action="reject", reason="wrong amount")

    assert resumed is not None
    assert resumed.approval_status == "rejected"
    assert resumed.status == "rejected"
    assert "wrong amount" in resumed.response
    assert resumed.tools_used == []

    # Rejection must not dispatch anything.
    async with async_session_factory() as s:
        logs = (
            await s.execute(select(ExecutionLog).where(ExecutionLog.request_id == res.run_id))
        ).scalars().all()
    assert logs == []

    async with async_session_factory() as s:
        appr = (
            await s.execute(select(ApprovalRequest).where(ApprovalRequest.run_id == res.run_id))
        ).scalars().first()
    assert appr.status == "rejected"


@pytest.mark.asyncio
async def test_resuming_an_unknown_run_returns_none(catalog):
    assert await resume_agent(run_id="req-does-not-exist", action="approve") is None


@pytest.mark.asyncio
async def test_pending_approval_survives_a_restart(catalog):
    """
    A run waiting on a human must still be resumable after the process restarts.

    Closing the checkpointer drops the connection and the compiled graph, so the resume
    below has to reopen the checkpoint store from disk and find the suspended run. This
    is why the checkpointer is SQLite-backed rather than in-memory.
    """
    from app.graph.workflow import close_checkpointer, get_checkpointer

    if type(await get_checkpointer()).__name__ == "MemorySaver":
        pytest.skip("SQLite checkpointer unavailable in this environment")

    res = await run_agent(
        query="Send an invoice for $50 to client@example.com",
        user_id="u-restart",
        session_id="s-restart-1",
    )
    assert res.status == "waiting_approval"

    # Simulate the process going away.
    await close_checkpointer()

    resumed = await resume_agent(run_id=res.run_id, action="approve")

    assert resumed is not None, "suspended run was lost across the restart"
    assert resumed.status == "completed"
    assert any("invoice" in t for t in resumed.tools_used)


@pytest.mark.asyncio
async def test_resume_is_not_repeatable(catalog):
    """A second resume of the same run must not re-execute the action."""
    res = await run_agent(
        query="Send an invoice for $50 to client@example.com",
        user_id="u-once",
        session_id="s-once-1",
    )
    first = await resume_agent(run_id=res.run_id, action="approve")
    assert first is not None and first.status == "completed"

    async with async_session_factory() as s:
        before = len(
            (
                await s.execute(select(ExecutionLog).where(ExecutionLog.request_id == res.run_id))
            ).scalars().all()
        )

    second = await resume_agent(run_id=res.run_id, action="approve")

    async with async_session_factory() as s:
        after = len(
            (
                await s.execute(select(ExecutionLog).where(ExecutionLog.request_id == res.run_id))
            ).scalars().all()
        )

    assert second is None, "a completed run should not be resumable"
    assert after == before, "resuming twice executed the action again"


@pytest.mark.asyncio
async def test_conversation_history_is_replayed_into_later_turns(catalog):
    session = "s-memory-1"
    await run_agent(
        query="What are the valid refund reason codes?",
        user_id="u-mem",
        session_id=session,
    )
    await run_agent(query="What about supported currencies?", user_id="u-mem", session_id=session)

    history = await _load_conversation_history(session)

    roles = [m["role"] for m in history]
    assert roles.count("user") >= 2
    assert roles.count("assistant") >= 2
    # Oldest first, so the planner reads the conversation in order.
    assert "refund reason codes" in history[0]["content"]
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"

    # History is scoped to its own session.
    assert await _load_conversation_history("s-memory-unrelated") == []


@pytest.mark.asyncio
async def test_history_reaches_the_state_the_planner_sees(catalog):
    """
    The replayed turns must land in graph state, not just in the loader.

    Read back the checkpointed state for the second turn's thread and confirm the first
    turn is present ahead of the new question.
    """
    from app.graph.workflow import get_agent_graph

    session = "s-memory-2"
    await run_agent(query="What are the refund reason codes?", user_id="u-mem2", session_id=session)
    second = await run_agent(
        query="And the supported currencies?", user_id="u-mem2", session_id=session
    )

    graph = await get_agent_graph()
    snapshot = await graph.aget_state({"configurable": {"thread_id": second.run_id}})
    messages = (snapshot.values or {}).get("messages", [])

    contents = [m["content"] for m in messages]
    assert any("refund reason codes" in c for c in contents), contents
    assert contents[-1] == "And the supported currencies?"
    # The prior turn precedes the new question, so the model reads them in order.
    assert next(i for i, c in enumerate(contents) if "refund reason codes" in c) < len(contents) - 1
