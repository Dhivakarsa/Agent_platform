"""
Canonical AgentState TypedDict schema adhering strictly to the Datazoic Implementation Plan (Section 7).
Tracks execution context, bounded candidate schemas, policy checks, HITL approval, and timeline events.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    """
    Complete state payload tracked throughout an authenticated agent workflow turn.
    Contains both conversational metadata and strict execution-level contracts.
    """
    # Identifiers & Auth Context
    request_id: str
    task_run_id: str
    thread_id: str
    user_id: str
    session_id: str
    user_role: str
    user_prompt: str

    # Intent Analysis & Registry Scoping
    intent: Optional[str]
    intent_confidence: float
    missing_fields: List[str]
    requires_clarification: bool
    sub_intents: List[str]
    selected_registries: List[str]

    # Candidate Retrieval (strictly bounded top-k tools)
    candidate_tool_ids: List[str]
    candidate_tools: List[Dict[str, Any]]
    candidate_summaries: List[Dict[str, Any]]

    # Planning & Human-in-the-Loop Policy Gate
    plan: List[Dict[str, Any]]
    execution_plan: List[str]
    dependency_mapping: Dict[str, Any]
    current_step_index: int
    pending_tool_call: Optional[Dict[str, Any]]
    approval_status: str  # "not_required", "pending", "approved", "rejected"
    approval_request_id: Optional[str]
    pending_approval: Optional[Dict[str, Any]]
    policy_decision: Optional[str]

    # Execution & Loop Control
    execution_log: List[Dict[str, Any]]
    execution_trace: List[Dict[str, Any]]
    last_result: Optional[Dict[str, Any]]
    next_action: str  # "replan", "execute_next", "finish"
    has_next_step: bool
    iteration_count: int
    max_iterations: int

    # Observability, Timeline & Token Accounting
    timeline_events: List[Dict[str, Any]]
    token_usage: Dict[str, int]  # {"input_tokens": int, "output_tokens": int, "total_tokens": int}
    error_context: Optional[str]
    final_response: Optional[str]
    messages: List[Dict[str, Any]]
