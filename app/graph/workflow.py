"""
StateGraph definition and compiled LangGraph workflow runner with explicit 10-node pipeline.
Implements candidate gate, schema-constrained retrieval, HITL policy approval, and token tracking.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from app.db.models import AgentRun
from app.db.session import async_session_factory, init_db
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


def create_agent_graph() -> Any:
    """Constructs and compiles the explicit 10-node LangGraph pipeline."""
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
        if state.get("approval_status") == "pending":
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
        if state.get("has_next_step") and state.get("iteration_count", 0) < state.get("max_iterations", 10):
            return "policy_and_approval_gate"
        return "compose_final_answer"

    workflow.add_conditional_edges(
        "evaluate_result",
        after_evaluate_result,
        {
            "policy_and_approval_gate": "policy_and_approval_gate",
            "compose_final_answer": "compose_final_answer",
        },
    )

    # 8. Complete turn
    workflow.add_edge("compose_final_answer", END)

    return workflow.compile()


_compiled_graph: Optional[Any] = None


def get_agent_graph() -> Any:
    """Return singleton compiled LangGraph instance."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = create_agent_graph()
    return _compiled_graph


async def run_agent(
    query: str,
    user_id: str = "default_user",
    user_role: str = "user",
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> ChatResponse:
    """Execute an authenticated turn through the explicit 10-stage LangGraph pipeline."""
    await init_db()
    from app.main import seed_system_catalog
    await seed_system_catalog()

    sess_id = session_id or str(uuid.uuid4())
    req_id = request_id or f"req-{uuid.uuid4().hex[:8]}"
    start_time = time.perf_counter()

    graph = get_agent_graph()

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
        "messages": [{"role": "user", "content": query}],
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

    logger.info("Executing 10-node Agent [user=%s, role=%s, req=%s]", user_id, user_role, req_id)
    final_state = await graph.ainvoke(initial_state)

    duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
    tools_used = [
        step["tool_name"]
        for step in final_state.get("execution_trace", [])
        if step.get("step") == "execution" and "tool_name" in step
    ]

    status_str = "waiting_approval" if final_state.get("approval_status") == "pending" else "completed"

    # Save AgentRun in database
    async with async_session_factory() as s:
        run_record = AgentRun(
            run_id=req_id,
            user_id=user_id,
            session_id=sess_id,
            query=query,
            status=status_str,
            tools_used=tools_used,
            execution_trace=final_state.get("execution_trace", []),
            approval_status=final_state.get("approval_status", "not_required"),
            duration_ms=duration_ms,
        )
        s.add(run_record)
        await s.commit()

    return ChatResponse(
        conversation_id=sess_id,
        run_id=req_id,
        response=final_state.get("final_response") or "Task completed successfully.",
        status=status_str,
        approval_status=final_state.get("approval_status", "not_required"),
        pending_approval=final_state.get("pending_approval"),
        tools_used=tools_used,
        execution_trace=final_state.get("execution_trace", []),
        timeline_events=final_state.get("timeline_events", []),
        token_usage=final_state.get("token_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
    )
