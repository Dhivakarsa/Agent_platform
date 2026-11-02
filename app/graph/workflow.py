"""
StateGraph definition and compiled LangGraph workflow runner with explicit 10-node pipeline.
Implements candidate gate, schema-constrained retrieval, HITL policy approval, and token tracking.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import time
import uuid
from typing import Any, Optional

import aiosqlite
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import desc, select

from app.config import get_settings
from app.db.models import AgentRun, ApprovalRequest
from app.db.session import async_session_factory
from app.services.bootstrap import ensure_platform_ready
from app.graph.nodes import (
    analyze_intent_node,
    compose_final_answer_node,
    evaluate_result_node,
    execute_tool_node,
    normalize_input_node,
    plan_and_select_node,
    policy_and_approval_gate_node,
    retrieve_tools_node,
    select_registries_node,
    validate_candidates_node,
)
from app.graph.state import AgentState
from app.schemas.chat import ChatResponse

logger = logging.getLogger(__name__)


def create_agent_graph(checkpointer: Any = None) -> Any:
    """
    Construct and compile the explicit 10-node LangGraph pipeline.

    A checkpointer is required for the human-in-the-loop pause: the policy gate calls
    ``interrupt()``, which can only suspend and resume if graph state is persisted.
    """
    workflow = StateGraph(AgentState)

    # 1. Register 10 Nodes
    workflow.add_node("normalize_input", normalize_input_node)
    workflow.add_node("analyze_intent", analyze_intent_node)
    workflow.add_node("select_registries", select_registries_node)
    workflow.add_node("retrieve_tools", retrieve_tools_node)
    workflow.add_node("validate_candidates", validate_candidates_node)
    workflow.add_node("plan_and_select", plan_and_select_node)
    workflow.add_node("policy_and_approval_gate", policy_and_approval_gate_node)
    workflow.add_node("execute_tool", execute_tool_node)
    workflow.add_node("evaluate_result", evaluate_result_node)
    workflow.add_node("compose_final_answer", compose_final_answer_node)

    # 2. Sequential Edges
    workflow.add_edge(START, "normalize_input")
    workflow.add_edge("normalize_input", "analyze_intent")
    workflow.add_edge("analyze_intent", "select_registries")
    workflow.add_edge("select_registries", "retrieve_tools")
    workflow.add_edge("retrieve_tools", "validate_candidates")

    # 3. Candidate Gate Routing
    def after_validate_candidates(state: AgentState) -> str:
        if state.get("requires_clarification"):
            return "compose_final_answer"
        return "plan_and_select"

    workflow.add_conditional_edges(
        "validate_candidates",
        after_validate_candidates,
        {
            "compose_final_answer": "compose_final_answer",
            "plan_and_select": "plan_and_select",
        },
    )

    # 4. Plan & Select Routing
    def after_plan_and_select(state: AgentState) -> str:
        if not state.get("pending_tool_call"):
            return "compose_final_answer"
        return "policy_and_approval_gate"

    workflow.add_conditional_edges(
        "plan_and_select",
        after_plan_and_select,
        {
            "compose_final_answer": "compose_final_answer",
            "policy_and_approval_gate": "policy_and_approval_gate",
        },
    )

    # 5. Policy & Approval Gate Routing
    def after_policy_gate(state: AgentState) -> str:
        """
        A pending approval never reaches here: the gate suspends inside the node via
        ``interrupt()``. What this decides is where a *settled* decision goes.
        """
        if state.get("approval_status") == "rejected" or not state.get("pending_tool_call"):
            return "compose_final_answer"
        return "execute_tool"

    workflow.add_conditional_edges(
        "policy_and_approval_gate",
        after_policy_gate,
        {
            "compose_final_answer": "compose_final_answer",
            "execute_tool": "execute_tool",
        },
    )

    # 6. Execution -> Evaluation
    workflow.add_edge("execute_tool", "evaluate_result")

    # 7. Evaluation Loop Routing
    def after_evaluate_result(state: AgentState) -> str:
        """
        Three-way loop control.

        "replan" returns to the planner so the reasoning model can choose the next tool
        with every completed step's results in context. "execute_next" is the deterministic
        fallback's pre-computed chain, which skips straight to the policy gate.
        """
        if state.get("iteration_count", 0) >= state.get("max_iterations", 10):
            return "compose_final_answer"

        action = state.get("next_action", "finish")
        if action == "replan":
            return "plan_and_select"
        if action == "execute_next" and state.get("pending_tool_call"):
            return "policy_and_approval_gate"
        return "compose_final_answer"

    workflow.add_conditional_edges(
        "evaluate_result",
        after_evaluate_result,
        {
            "plan_and_select": "plan_and_select",
            "policy_and_approval_gate": "policy_and_approval_gate",
            "compose_final_answer": "compose_final_answer",
        },
    )

    # 8. Complete turn
    workflow.add_edge("compose_final_answer", END)

    return workflow.compile(checkpointer=checkpointer)


_compiled_graph: Optional[Any] = None
_checkpointer: Optional[Any] = None
_checkpoint_conn: Optional[Any] = None
# The loop the cached connection belongs to; see get_checkpointer.
_checkpoint_loop: Optional[Any] = None

# How many previous turns of a conversation to replay into a new turn's context.
_HISTORY_TURNS = 6


def _register_worker_shutdown(conn: Any) -> None:
    """
    Make sure the interpreter can exit even if the checkpointer is never closed.

    aiosqlite drives the connection from a non-daemon worker thread, so a script or
    notebook that calls ``run_agent`` directly and never calls ``close_checkpointer``
    would hang at exit. The worker stops when a queued call returns its stop sentinel, so
    that is enqueued at interpreter shutdown. The daemon flag cannot be used instead: the
    thread is already running by the time ``connect()`` returns.

    Entirely best-effort. The API still closes the connection cleanly on shutdown, and
    SQLite recovers its journal on the next open regardless.
    """

    def _stop() -> None:
        try:
            from aiosqlite.core import _STOP_RUNNING_SENTINEL

            tx = getattr(conn, "_tx", None)
            if tx is not None:
                tx.put_nowait((None, lambda: _STOP_RUNNING_SENTINEL))
        except Exception:
            pass

    atexit.register(_stop)


async def get_checkpointer() -> Any:
    """
    Return the process-wide checkpointer, creating it on first use.

    Backed by SQLite so a run that is waiting on human approval survives a restart; if
    that backend cannot be opened the graph still works, with in-memory checkpoints.
    """
    global _checkpointer, _checkpoint_conn, _checkpoint_loop, _compiled_graph

    loop = asyncio.get_running_loop()
    if _checkpointer is not None and _checkpoint_loop is loop:
        return _checkpointer

    if _checkpointer is not None:
        # A different event loop is running: the cached sqlite connection belongs to the
        # previous loop and would block if used here, so drop it and rebuild.
        _checkpoint_conn = None
        _checkpointer = None
        _compiled_graph = None

    settings = get_settings()
    db_url = settings.database_url
    if db_url.startswith("sqlite"):
        # Keep checkpoints beside the application database.
        db_path = db_url.split("///")[-1] or "agentic_system.db"
        checkpoint_path = f"{db_path}.checkpoints"
    else:
        checkpoint_path = "agent_checkpoints.db"

    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        _checkpoint_conn = await aiosqlite.connect(checkpoint_path)
        _register_worker_shutdown(_checkpoint_conn)
        saver = AsyncSqliteSaver(_checkpoint_conn)
        await saver.setup()
        _checkpointer = saver
        _checkpoint_loop = loop
        logger.info("Agent checkpointer ready (sqlite: %s)", checkpoint_path)
    except Exception as exc:
        logger.warning(
            "SQLite checkpointer unavailable (%s); falling back to in-memory checkpoints. "
            "Approvals will not survive a restart.",
            exc,
        )
        _checkpointer = MemorySaver()
        _checkpoint_loop = loop

    return _checkpointer


async def get_agent_graph() -> Any:
    """Return the compiled LangGraph instance for the running event loop."""
    global _compiled_graph
    checkpointer = await get_checkpointer()  # may reset _compiled_graph on a loop change
    if _compiled_graph is None:
        _compiled_graph = create_agent_graph(checkpointer=checkpointer)
    return _compiled_graph


async def close_checkpointer() -> None:
    """Close the checkpoint connection on application shutdown."""
    global _checkpointer, _checkpoint_conn, _checkpoint_loop, _compiled_graph
    if _checkpoint_conn is not None:
        try:
            await _checkpoint_conn.close()
        except Exception:
            pass
    _checkpoint_conn = None
    _checkpointer = None
    _checkpoint_loop = None
    _compiled_graph = None


async def _load_conversation_history(session_id: str, limit: int = _HISTORY_TURNS) -> list[dict[str, Any]]:
    """
    Rebuild recent conversation turns for a session from the run history.

    Held in the relational store rather than graph state so that history is independent
    of checkpoint lifetime and visible to the Logs and Usage screens. This is what lets
    the planner resolve a follow-up such as "send it to john@example.com".
    """
    try:
        async with async_session_factory() as s:
            rows = (
                await s.execute(
                    select(AgentRun)
                    .where(AgentRun.session_id == session_id)
                    .order_by(desc(AgentRun.id))
                    .limit(limit)
                )
            ).scalars().all()
    except Exception as exc:
        logger.warning("Could not load conversation history for %s: %s", session_id, exc)
        return []

    messages: list[dict[str, Any]] = []
    for row in reversed(rows):  # oldest first
        if row.query:
            messages.append({"role": "user", "content": row.query})
        if row.final_response:
            messages.append({"role": "assistant", "content": row.final_response})
    return messages


def _interrupt_payload(result: Any) -> Optional[dict[str, Any]]:
    """Extract the approval payload when a run suspended at the policy gate."""
    interrupts = (result or {}).get("__interrupt__") if isinstance(result, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", None)
    if value is None and isinstance(first, dict):
        value = first.get("value")
    return value if isinstance(value, dict) else None


def _tools_used(state: dict[str, Any]) -> list[str]:
    return [
        step["tool_name"]
        for step in state.get("execution_trace", [])
        if step.get("step") == "execution" and "tool_name" in step
    ]


async def _finalize(
    *,
    run_id: str,
    user_id: str,
    session_id: str,
    query: str,
    state: dict[str, Any],
    pending_approval: Optional[dict[str, Any]],
    duration_ms: float,
) -> ChatResponse:
    """Persist the run outcome and build the API response."""
    tools = _tools_used(state)

    if pending_approval is not None:
        status_str = "waiting_approval"
        approval_status = "pending"
        response_text = (
            f"\u26a0\ufe0f **Approval required** before I run "
            f"`{pending_approval.get('tool_name')}`.\n\n"
            f"Reason: {'; '.join(pending_approval.get('reasons') or ['policy'])}.\n\n"
            f"Please confirm or cancel below."
        )
    else:
        approval_status = state.get("approval_status", "not_required")
        status_str = "rejected" if approval_status == "rejected" else "completed"
        response_text = state.get("final_response") or "Task completed successfully."

    async with async_session_factory() as s:
        existing = (
            await s.execute(select(AgentRun).where(AgentRun.run_id == run_id))
        ).scalar_one_or_none()
        if existing:
            existing.status = status_str
            existing.approval_status = approval_status
            existing.tools_used = tools
            existing.execution_trace = state.get("execution_trace", [])
            existing.final_response = response_text
            existing.duration_ms = duration_ms
        else:
            s.add(
                AgentRun(
                    run_id=run_id,
                    user_id=user_id,
                    session_id=session_id,
                    query=query,
                    status=status_str,
                    tools_used=tools,
                    execution_trace=state.get("execution_trace", []),
                    approval_status=approval_status,
                    final_response=response_text,
                    duration_ms=duration_ms,
                )
            )
        await s.commit()

    return ChatResponse(
        conversation_id=session_id,
        run_id=run_id,
        response=response_text,
        status=status_str,
        approval_status=approval_status,
        pending_approval=pending_approval,
        tools_used=tools,
        execution_trace=state.get("execution_trace", []),
        timeline_events=state.get("timeline_events", []),
        token_usage=state.get(
            "token_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        ),
    )


async def run_agent(
    query: str,
    user_id: str = "default_user",
    user_role: str = "user",
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> ChatResponse:
    """Execute one authenticated turn through the 10-stage LangGraph pipeline."""
    # Idempotent: creates the schema and seeds the catalog only the first time, so a turn
    # started outside the API lifespan still works without re-running startup work.
    await ensure_platform_ready()

    sess_id = session_id or str(uuid.uuid4())
    req_id = request_id or f"req-{uuid.uuid4().hex[:8]}"
    start_time = time.perf_counter()

    graph = await get_agent_graph()

    # One checkpoint thread per run: a run paused for approval cannot be disturbed by the
    # next message on the same conversation. Continuity comes from replayed history.
    config = {"configurable": {"thread_id": req_id}}

    history = await _load_conversation_history(sess_id)

    initial_state: AgentState = {
        "request_id": req_id,
        "task_run_id": f"task-{uuid.uuid4().hex[:8]}",
        "thread_id": sess_id,
        "user_id": user_id,
        "session_id": sess_id,
        "user_role": user_role,
        "user_prompt": query,
        "sub_intents": [],
        "candidate_tools": [],
        "candidate_tool_ids": [],
        "candidate_summaries": [],
        "messages": history + [{"role": "user", "content": query}],
        "dependency_mapping": {},
        "plan": [],
        "execution_plan": [],
        "pending_tool_call": None,
        "approval_status": "not_required",
        "pending_approval": None,
        "iteration_count": 0,
        "max_iterations": 10,
        "execution_log": [],
        "execution_trace": [],
        "timeline_events": [],
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "error_context": None,
        "final_response": None,
    }

    logger.info(
        "Executing agent [user=%s, role=%s, req=%s, history_turns=%d]",
        user_id,
        user_role,
        req_id,
        len(history) // 2,
    )
    result = await graph.ainvoke(initial_state, config=config)

    pending_approval = _interrupt_payload(result)
    state = result if isinstance(result, dict) else {}
    if pending_approval is not None:
        # The graph is suspended; read the state captured at the interrupt.
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values or {})

    return await _finalize(
        run_id=req_id,
        user_id=user_id,
        session_id=sess_id,
        query=query,
        state=state,
        pending_approval=pending_approval,
        duration_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
    )


async def resume_agent(
    run_id: str,
    action: str,
    reason: Optional[str] = None,
) -> Optional[ChatResponse]:
    """
    Resume a run that is suspended at the approval gate.

    Returns None when there is no such suspended run. The graph continues from inside the
    policy gate, so an approved action proceeds through execution, evaluation and any
    remaining steps of the plan instead of being dispatched in isolation.
    """
    graph = await get_agent_graph()
    config = {"configurable": {"thread_id": run_id}}

    snapshot = await graph.aget_state(config)
    if snapshot is None or not getattr(snapshot, "next", None):
        return None

    async with async_session_factory() as s:
        run = (
            await s.execute(select(AgentRun).where(AgentRun.run_id == run_id))
        ).scalar_one_or_none()

    start_time = time.perf_counter()
    result = await graph.ainvoke(
        Command(resume={"action": action, "reason": reason}),
        config=config,
    )

    pending_approval = _interrupt_payload(result)
    state = result if isinstance(result, dict) else {}
    if pending_approval is not None:
        # A later step in the same plan needs its own approval.
        state = dict((await graph.aget_state(config)).values or {})

    prior_ms = (run.duration_ms if run else 0.0) or 0.0
    return await _finalize(
        run_id=run_id,
        user_id=(run.user_id if run else state.get("user_id", "default_user")),
        session_id=(run.session_id if run else state.get("session_id", run_id)),
        query=(run.query if run else state.get("user_prompt", "")),
        state=state,
        pending_approval=pending_approval,
        duration_ms=round(prior_ms + (time.perf_counter() - start_time) * 1000.0, 2),
    )
