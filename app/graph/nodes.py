"""
Explicit 10-node LangGraph pipeline adhering strictly to the Datazoic Implementation Plan (Section 6 & 7).
Nodes:
  1. normalize_input
  2. analyze_intent
  3. select_registries
  4. retrieve_tools (bounded top-k candidate schemas)
  5. validate_candidates (candidate gate)
  6. plan_and_select (schema-constrained planning)
  7. policy_and_approval_gate (risk tier & HITL approval check)
  8. execute_tool (sandboxed execution engine dispatch)
  9. evaluate_result (loop control & multi-step execution)
  10. compose_final_answer (observability, timeline & token accounting)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
import uuid

from langgraph.types import interrupt
from sqlalchemy import select

from app.config import get_settings
from app.db.models import AgentRun, ApprovalRequest
from app.db.session import async_session_factory
from app.graph.state import AgentState
from app.meta_tools.rag_pipeline_tool import get_rag_tool
from app.schemas.tool import ApprovalPolicy, ExecutionStatus, RegisteredTool, RiskLevel
from app.services.execution_engine import get_execution_engine
from app.services.llm_service import get_llm_service
from app.services.token_usage_service import get_token_usage_service
from app.services.tool_registry import get_tool_registry
from app.services.tool_retriever import get_tool_retriever

logger = logging.getLogger(__name__)

_EMPTY_TOKENS: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _accumulate_tokens(current: Dict[str, int], addition: Dict[str, int]) -> Dict[str, int]:
    """Sum token counters across the several model calls made during one turn."""
    merged = {
        "input_tokens": int(current.get("input_tokens", 0)) + int(addition.get("input_tokens", 0)),
        "output_tokens": int(current.get("output_tokens", 0)) + int(addition.get("output_tokens", 0)),
    }
    merged["total_tokens"] = merged["input_tokens"] + merged["output_tokens"]
    return merged


def _completed_step_summaries(state: AgentState) -> List[Dict[str, Any]]:
    """Condense already-executed steps so the planner can chain multi-step work."""
    summaries: List[Dict[str, Any]] = []
    for step in state.get("execution_trace", []):
        if step.get("step") != "execution":
            continue
        summaries.append(
            {
                "tool": step.get("tool_name"),
                "arguments": step.get("arguments", {}),
                "status": step.get("status"),
                "result": step.get("response"),
            }
        )
    return summaries


# ── 1. normalize_input ────────────────────────────────────────────────────────


async def normalize_input_node(state: AgentState) -> dict[str, Any]:
    """Validate incoming query, establish unique task and thread IDs, and initialize timeline."""
    query = state.get("user_prompt", "").strip()
    req_id = state.get("request_id") or f"req-{uuid.uuid4().hex[:8]}"
    task_id = state.get("task_run_id") or f"task-{uuid.uuid4().hex[:8]}"
    thread_id = state.get("thread_id") or state.get("session_id") or str(uuid.uuid4())

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "normalize_input",
            "status": "completed",
            "timestamp": time.time(),
            "details": f"Request {req_id} normalized for thread {thread_id[:8]}.",
        }
    )

    tokens = state.get("token_usage") or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    return {
        "request_id": req_id,
        "task_run_id": task_id,
        "thread_id": thread_id,
        "user_prompt": query,
        "timeline_events": timeline,
        "token_usage": tokens,
        "execution_log": list(state.get("execution_log", [])),
        "execution_trace": list(state.get("execution_trace", [])),
        "iteration_count": state.get("iteration_count", 0),
        "max_iterations": state.get("max_iterations", 10),
        "sub_intents": [],
        "missing_fields": [],
        "requires_clarification": False,
        "approval_status": state.get("approval_status", "not_required"),
    }


# ── 2. analyze_intent ─────────────────────────────────────────────────────────


async def analyze_intent_node(state: AgentState) -> dict[str, Any]:
    """Classify primary intent, extract structured entities, and flag missing mandatory parameters."""
    query = state["user_prompt"]
    q_lower = query.lower()

    # Regex entity extraction
    extracted: Dict[str, Any] = dict(state.get("dependency_mapping", {}))
    missing_fields: List[str] = []
    requires_clarification = False

    # Extract email (with auto-sanitization for typos like 'hotmail,com' -> 'hotmail.com')
    cleaned_query = re.sub(r"@([\w\.-]+),([a-zA-Z]{2,})", r"@\1.\2", query)
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", cleaned_query)
    if email_match:
        extracted["email"] = email_match.group(0)
        extracted["recipient"] = email_match.group(0)

    # Extract amount & currency
    amount_match = re.search(r"\$(\d+(?:\.\d{2})?)|\b(\d+(?:\.\d{2})?)\s*(?:USD|dollars)", query, re.IGNORECASE)
    if amount_match:
        val = amount_match.group(1) or amount_match.group(2)
        extracted["amount"] = f"{float(val):.2f}"
        extracted["currency_code"] = "USD"

    # Extract identifier tokens
    id_match = re.search(r"\b(user_\w+|INV2-[\w-]+|ORDER-[\w-]+|TX-[\w-]+|PP-D-[\w-]+)\b", query, re.IGNORECASE)
    if id_match:
        token = id_match.group(0).upper()
        if token.startswith("INV2-"):
            extracted["invoice_id"] = token
        elif token.startswith("ORDER-"):
            extracted["order_id"] = token
        elif token.startswith("TX-"):
            extracted["transaction_id"] = token
        elif token.startswith("PP-D-"):
            extracted["dispute_id"] = token

    # Intent Classification (ordered by domain specificity with word boundaries)
    intent = "GENERAL_QA"
    confidence = 0.85

    if re.search(
        r"\b(what tools|which tools|tools are available|available tools|capabilities|"
        r"last request|my last|status of my|execution log|what can you do)\b",
        q_lower,
    ):
        intent = "SYSTEM_INTROSPECTION"
    elif re.search(r"\b(calculate|math|multiply|divide|percentage|sqrt)\b", q_lower) or any(c in q_lower for c in ["+", "*", "/"]):
        intent = "CALCULATION"
    elif re.search(r"\b(time|timezone|clock|today|date)\b", q_lower):
        intent = "TIME"
    elif re.search(
        r"\b(documentation|docs|policy|policies|fee|fees|guide|faq|"
        r"reason codes?|error codes?|status codes?|troubleshoot|"
        r"supported currenc\w*|which currenc\w*|lifecycle|idempotency)\b",
        q_lower,
    ) or re.search(r"\b(what|which) (are|is) the (valid|allowed|supported|standard)\b", q_lower):
        intent = "KNOWLEDGE_RAG"
    elif re.search(r"\b(sales|volume|sales volume|revenue|analytics|report|reports|statement)\b", q_lower):
        intent = "REPORTING"
        if re.search(r"\b(email|mail|send|recipient)\b", q_lower) or extracted.get("email"):
            sub_intents = ["REPORTING", "COMMUNICATION"]
    elif re.search(r"\b(email|mail|message)\b", q_lower) and not re.search(r"\b(invoice|invoices|bill|bills)\b", q_lower):
        intent = "COMMUNICATION"
        if not extracted.get("email") and not extracted.get("recipient"):
            missing_fields.append("recipient_email")
            requires_clarification = True
    elif re.search(r"\b(invoice|invoices|invoicing|bill|bills)\b", q_lower):
        intent = "INVOICING"
        if any(w in q_lower for w in ["create", "draft", "make", "generate"]) and not extracted.get("amount"):
            missing_fields.append("invoice_amount")
            requires_clarification = True
        if any(w in q_lower for w in ["send", "to"]) and not extracted.get("email") and not extracted.get("recipient") and not extracted.get("invoice_id"):
            missing_fields.append("recipient_email")
            requires_clarification = True
    elif re.search(r"\b(order|orders|capture|refund|checkout|payment|payments)\b", q_lower):
        intent = "PAYMENTS"
    elif re.search(r"\b(dispute|disputes|claim|claims|chargeback|chargebacks)\b", q_lower):
        intent = "DISPUTES"

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "analyze_intent",
            "status": "completed",
            "timestamp": time.time(),
            "details": f"Intent: {intent} (confidence={confidence:.2f})",
            "metadata": {
                "intent": intent,
                "missing_fields": missing_fields,
                "requires_clarification": requires_clarification,
            },
        }
    )

    return {
        "intent": intent,
        "intent_confidence": confidence,
        "missing_fields": missing_fields,
        "requires_clarification": requires_clarification,
        "dependency_mapping": extracted,
        "timeline_events": timeline,
    }


# ── 3. select_registries ──────────────────────────────────────────────────────


async def select_registries_node(state: AgentState) -> dict[str, Any]:
    """Scope target namespaces based on intent to prevent catalog bloat."""
    intent = state.get("intent", "GENERAL_QA")
    selected: List[str] = []

    if intent in ["CALCULATION", "TIME", "SYSTEM_INTROSPECTION"]:
        selected = ["general"]
    elif intent == "COMMUNICATION":
        selected = ["communication", "general"]
    elif intent in ["INVOICING", "PAYMENTS", "DISPUTES"]:
        selected = ["paypal"]
    elif intent == "REPORTING" or "REPORTING" in state.get("sub_intents", []):
        selected = ["paypal", "communication", "general"]
    elif intent == "KNOWLEDGE_RAG":
        selected = ["paypal", "general"]
    else:
        selected = ["paypal", "communication", "general", "filesystem"]

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "select_registries",
            "status": "completed",
            "timestamp": time.time(),
            "details": f"Scoped registries: {', '.join(selected)}",
        }
    )

    return {
        "selected_registries": selected,
        "timeline_events": timeline,
    }


# ── 4. retrieve_tools ─────────────────────────────────────────────────────────


async def retrieve_tools_node(state: AgentState) -> dict[str, Any]:
    """Retrieve strictly bounded top-k (8 to 12) candidate tool schemas matching selected registries."""
    retriever = get_tool_retriever()
    registry = get_tool_registry()
    query = state["user_prompt"]
    selected_regs = state.get("selected_registries", ["paypal"])

    # Retrieve candidates via hybrid search
    raw_candidates = retriever.retrieve(query=query, domain_hint=None, top_k=10)

    # Filter to selected registries/namespaces
    filtered_tools: List[RegisteredTool] = []
    for tool in raw_candidates:
        ns = getattr(tool, "namespace", None) or getattr(tool, "domain", None) or getattr(tool, "category", "")
        if ns in selected_regs or not selected_regs or ns == "general":
            filtered_tools.append(tool)

    # Fallback: if filtered tools empty, fetch directly from registry for that namespace
    if not filtered_tools:
        all_tools = await registry.list_tools()
        filtered_tools = [
            t for t in all_tools
            if (getattr(t, "namespace", "") in selected_regs or getattr(t, "category", "") in selected_regs)
        ][:10]

    bounded_tools = filtered_tools[:10]

    # Pin send_email when communication sub-intent is present in a composite task
    if "COMMUNICATION" in state.get("sub_intents", []) or "send" in query.lower():
        email_tool = registry.get_tool("send_email")
        if email_tool and email_tool.name not in [t.name for t in bounded_tools]:
            bounded_tools.append(email_tool)

    # The two meta-tools are pinned: always offered to the planner regardless of retrieval
    # score, so the agent can always consult documentation or introspect the system itself.
    for pinned_name in ("rag_pipeline_tool", "system_search_tool"):
        pinned = registry.get_tool(pinned_name)
        if pinned and pinned.name not in [t.name for t in bounded_tools]:
            bounded_tools.append(pinned)

    candidate_ids = [t.name for t in bounded_tools]
    candidate_summaries = [
        {
            "name": t.name,
            "display_name": t.display_name,
            "risk_level": t.risk_level.value if hasattr(t.risk_level, "value") else str(t.risk_level),
            "approval_policy": t.approval_policy.value if hasattr(t.approval_policy, "value") else str(t.approval_policy),
            "description": t.description,
            "parameters": [p.model_dump() if hasattr(p, "model_dump") else p for p in t.parameters],
        }
        for t in bounded_tools
    ]

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "retrieve_tools",
            "status": "completed",
            "timestamp": time.time(),
            "details": f"Retrieved {len(candidate_ids)} bounded candidates: {', '.join(candidate_ids[:4])}...",
            "metadata": {"candidate_count": len(candidate_ids), "candidates": candidate_ids},
        }
    )

    return {
        "candidate_tool_ids": candidate_ids,
        "candidate_tools": [t.model_dump() for t in bounded_tools],
        "candidate_summaries": candidate_summaries,
        "timeline_events": timeline,
    }


# ── 5. validate_candidates ────────────────────────────────────────────────────


async def validate_candidates_node(state: AgentState) -> dict[str, Any]:
    """Candidate Gate: ensures candidate tools match intent and halts with clarification if required."""
    requires_clarification = state.get("requires_clarification", False)
    missing = state.get("missing_fields", [])
    intent = state.get("intent", "GENERAL_QA")

    clarification_msg = None
    if requires_clarification:
        if "invoice_amount" in missing:
            clarification_msg = "Please provide the invoice amount (e.g. $150.00) and recipient email to generate this invoice."
        elif "recipient_email" in missing:
            clarification_msg = "Please specify the recipient email address to proceed with this request."
        else:
            clarification_msg = f"Additional information required for this action: {', '.join(missing)}. Please provide details."

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "validate_candidates",
            "status": "clarification_needed" if requires_clarification else "completed",
            "timestamp": time.time(),
            "details": "Clarification required" if requires_clarification else "Candidate schemas validated.",
            "metadata": {"requires_clarification": requires_clarification, "missing_fields": missing},
        }
    )

    return {
        "policy_decision": "CLARIFICATION_REQUIRED" if requires_clarification else "VALIDATED",
        "final_response": clarification_msg,
        "timeline_events": timeline,
    }


# ── 6. plan_and_select ────────────────────────────────────────────────────────


# Entity keys that can satisfy a declared parameter, beyond an exact name match.
_PARAM_ALIASES: Dict[str, tuple[str, ...]] = {
    "invoice_id": ("invoice_id",),
    "order_id": ("order_id",),
    "transaction_id": ("transaction_id",),
    "dispute_id": ("dispute_id",),
    "recipient_email": ("email", "recipient"),
    "to": ("email", "recipient"),
    "email": ("email", "recipient"),
    "amount": ("amount",),
    "currency_code": ("currency_code",),
}


def _fill_from_entities(
    summary: Dict[str, Any], entities: Dict[str, Any]
) -> tuple[Dict[str, Any], List[str]]:
    """
    Populate a tool's parameters from extracted entities.

    Returns the arguments that could be filled and the names of required parameters
    that remain unsatisfied.
    """
    arguments: Dict[str, Any] = {}
    unmet: List[str] = []

    for param in summary.get("parameters", []) or []:
        name = param.get("name")
        if not name:
            continue
        for key in (name,) + _PARAM_ALIASES.get(name, ()):
            if entities.get(key) not in (None, ""):
                arguments[name] = entities[key]
                break
        else:
            if param.get("required"):
                unmet.append(name)

    return arguments, unmet


def _select_satisfiable_candidate(
    state: AgentState,
) -> Optional[tuple[str, Dict[str, Any]]]:
    """
    Pick the best candidate whose required parameters can all be filled.

    Candidates keep their retrieval ranking, so this returns the highest-ranked tool
    that is actually executable. Falls back to the best read-only candidate with no
    required parameters, so an under-specified request lists rather than guesses.
    """
    entities = state.get("dependency_mapping", {}) or {}
    summaries = state.get("candidate_summaries") or []

    for summary in summaries:
        arguments, unmet = _fill_from_entities(summary, entities)
        if not unmet:
            return summary["name"], arguments

    # Nothing fully satisfiable: prefer a listing/search tool that needs no input.
    for summary in summaries:
        name = summary.get("name", "")
        required = [p for p in summary.get("parameters", []) or [] if p.get("required")]
        if not required and any(verb in name for verb in ("list", "search", "show_all")):
            return name, {}

    return None


def _deterministic_plan(state: AgentState) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Rule-based planner used when the Gemini reasoning layer is unavailable.

    Keeps the platform demonstrable offline (no API key, hermetic tests) at the cost of
    only recognising the intents encoded below. Returns (plan, pending_tool_call).
    """
    intent = state.get("intent", "GENERAL_QA")
    query = state["user_prompt"]
    entities = state.get("dependency_mapping", {})
    candidates = state.get("candidate_tools", [])
    candidate_names = [t["name"] for t in candidates]

    pending_call: Optional[Dict[str, Any]] = None
    plan: List[Dict[str, Any]] = []

    # 1. Calculation
    if intent == "CALCULATION" and "calculator_evaluate" in candidate_names:
        # Extract expression
        expr = query
        for lead in ["calculate", "what is", "compute", "evaluate"]:
            if lead in expr.lower():
                expr = expr.lower().split(lead, 1)[-1].strip()
        expr = expr.replace("?", "").strip()
        pending_call = {"name": "calculator_evaluate", "arguments": {"expression": expr}}
        plan = [{"step": 1, "tool": "calculator_evaluate", "description": "Safe math expression evaluation"}]

    # 2. Time
    elif intent == "TIME" and "time_get_current" in candidate_names:
        tz = "UTC"
        if "est" in query.lower() or "new york" in query.lower():
            tz = "America/New_York"
        elif "ist" in query.lower() or "india" in query.lower():
            tz = "Asia/Kolkata"
        elif "pst" in query.lower() or "pacific" in query.lower():
            tz = "America/Los_Angeles"
        pending_call = {"name": "time_get_current", "arguments": {"timezone": tz}}
        plan = [{"step": 1, "tool": "time_get_current", "description": f"Fetch current timestamp in {tz}"}]

    # 3. Communication (Email)
    elif intent == "COMMUNICATION":
        if "send" in query.lower():
            tool_name = next((n for n in candidate_names if "send" in n and "email" in n), "send_email")
            pending_call = {
                "name": tool_name,
                "arguments": {
                    "to": entities.get("email") or entities.get("recipient", "customer@example.com"),
                    "subject": "Important Account Notification",
                    "body": f"Regarding your query: '{query}'",
                },
            }
            plan = [{"step": 1, "tool": tool_name, "description": "Dispatch outbound email to customer"}]
        else:
            tool_name = next((n for n in candidate_names if "draft" in n and "email" in n), "create_email_draft")
            pending_call = {
                "name": tool_name,
                "arguments": {
                    "to": entities.get("email") or entities.get("recipient", "customer@example.com"),
                    "subject": "Draft Notice",
                    "body": f"Draft content prepared from query: '{query}'",
                },
            }
            plan = [{"step": 1, "tool": tool_name, "description": "Draft email message"}]

    # 4. Invoicing
    elif intent == "INVOICING":
        if any(w in query.lower() for w in ["create", "draft", "make", "generate"]):
            tool_name = next((n for n in candidate_names if "create" in n and "invoice" in n), "create_draft_invoice")
            pending_call = {
                "name": tool_name,
                "arguments": {
                    "amount": entities.get("amount", "100.00"),
                    "currency_code": entities.get("currency_code", "USD"),
                    "recipient_email": entities.get("email", "client@example.com"),
                },
            }
            plan = [
                {"step": 1, "tool": tool_name, "description": f"Create draft invoice for ${entities.get('amount', '100.00')}"}
            ]
        elif "send" in query.lower():
            tool_name = next((n for n in candidate_names if "send" in n and "invoice" in n), "send_invoice")
            pending_call = {
                "name": tool_name,
                "arguments": {
                    "invoice_id": entities.get("invoice_id", f"INV2-{uuid.uuid4().hex[:8].upper()}"),
                    "recipient_email": entities.get("email") or entities.get("recipient"),
                    "amount": entities.get("amount", "50.00"),
                    "currency_code": entities.get("currency_code", "USD"),
                },
            }
            plan = [{"step": 1, "tool": tool_name, "description": f"Send invoice for ${entities.get('amount', '50.00')} to {entities.get('email', 'recipient')}"}]
        else:
            tool_name = next((n for n in candidate_names if "list" in n or "search" in n), candidate_names[0] if candidate_names else None)
            if tool_name:
                pending_call = {"name": tool_name, "arguments": {}}
                plan = [{"step": 1, "tool": tool_name, "description": f"Execute {tool_name}"}]

    # 5. Reporting & Analytics (Multi-Step DAG capable)
    elif intent == "REPORTING":
        report_tool = next((n for n in candidate_names if "report" in n or "transaction" in n), "paypal_get_transaction_report")
        if "send" in query.lower() or entities.get("email"):
            email_tool = next((n for n in candidate_names if "send" in n and "email" in n), "send_email")
            plan = [
                {
                    "step": 1,
                    "tool": report_tool,
                    "description": "Fetch transaction and sales volume report for requested period",
                    "arguments": {"start_date": "2026-10-01", "end_date": "2026-10-31", "fields": "all"},
                },
                {
                    "step": 2,
                    "tool": email_tool,
                    "description": f"Dispatch sales volume report via Google SMTP to {entities.get('email', 'recipient')}",
                    "arguments": {
                        "to": entities.get("email") or entities.get("recipient"),
                        "subject": "Monthly Sales Volume & Financial Performance Report",
                        "body": "$DEPENDENCY:STEP_1_REPORT",
                    },
                },
            ]
            pending_call = {"name": report_tool, "arguments": plan[0]["arguments"]}
        else:
            pending_call = {"name": report_tool, "arguments": {"start_date": "2026-10-01", "end_date": "2026-10-31"}}
            plan = [{"step": 1, "tool": report_tool, "description": "Fetch transaction history and sales volume"}]

    # 6. Knowledge RAG
    elif intent == "KNOWLEDGE_RAG":
        pending_call = {"name": "rag_pipeline_tool", "arguments": {"query": query}}
        plan = [{"step": 1, "tool": "rag_pipeline_tool", "description": "Query documentation knowledge base"}]

    # 7. System introspection (capabilities catalog or past execution status)
    elif intent == "SYSTEM_INTROSPECTION":
        q_lower = query.lower()
        scope = "execution_logs" if re.search(r"\b(last|status|log|history|request)\b", q_lower) else "capabilities"
        keywords = " ".join(
            w for w in re.findall(r"\w+", q_lower)
            if w not in {"what", "which", "tools", "are", "available", "for", "managing", "my", "the", "is", "of", "status"}
        )
        pending_call = {"name": "system_search_tool", "arguments": {"scope": scope, "query": keywords.strip()}}
        plan = [{"step": 1, "tool": "system_search_tool", "description": f"Introspect system ({scope})"}]

    # 8. Fallback: best candidate whose required parameters can actually be filled.
    elif candidate_names:
        chosen = _select_satisfiable_candidate(state)
        if chosen:
            best_tool, arguments = chosen
            pending_call = {"name": best_tool, "arguments": arguments}
            plan = [
                {
                    "step": 1,
                    "tool": best_tool,
                    "description": f"Execute candidate {best_tool}",
                    "arguments": arguments,
                }
            ]

    return plan, pending_call


async def plan_and_select_node(state: AgentState) -> dict[str, Any]:
    """
    Select the next tool call, constrained to the bounded candidate shortlist.

    Primary path: Gemini function calling over the retrieved candidate schemas. The model
    sees only the top-k tools the retrieval stage selected, which is what keeps accuracy
    flat as the catalog grows from 50 to 1,000+ tools.

    Fallback path: the deterministic rule planner, used when no API key is configured or
    the model call fails.
    """
    # If clarification was requested upstream, do not plan tool calls.
    if state.get("requires_clarification"):
        return {"pending_tool_call": None, "plan": []}

    query = state["user_prompt"]
    candidates = state.get("candidate_summaries") or []
    timeline = list(state.get("timeline_events", []))
    tokens = dict(state.get("token_usage") or _EMPTY_TOKENS)

    plan: List[Dict[str, Any]] = []
    pending_call: Optional[Dict[str, Any]] = None
    direct_answer: Optional[str] = None
    planner_mode = "deterministic"

    llm = get_llm_service()
    choice = await llm.choose_tool(
        user_prompt=query,
        candidates=candidates,
        history=state.get("messages", []),
        completed_steps=_completed_step_summaries(state),
    )

    if choice is not None:
        planner_mode = "gemini_function_calling"
        tokens = _accumulate_tokens(tokens, choice.usage.as_dict())

        if choice.is_tool_call:
            pending_call = {"name": choice.tool_name, "arguments": choice.arguments}
            plan = [
                {
                    "step": 1,
                    "tool": choice.tool_name,
                    "description": choice.reasoning,
                    "arguments": choice.arguments,
                }
            ]
        elif choice.direct_answer:
            # The model answered without a tool, or asked for a missing parameter.
            direct_answer = choice.direct_answer

    if choice is None:
        plan, pending_call = _deterministic_plan(state)

    selected_name = pending_call.get("name") if pending_call else "None (Direct Answer)"
    timeline.append(
        {
            "stage": "plan_and_select",
            "status": "completed",
            "timestamp": time.time(),
            "details": f"Planned action: {selected_name}",
            "metadata": {
                "planned_tool": selected_name,
                "plan": plan,
                "planner": planner_mode,
                "candidates_considered": len(candidates),
            },
        }
    )

    result: dict[str, Any] = {
        "plan": plan,
        "pending_tool_call": pending_call,
        "timeline_events": timeline,
        "token_usage": tokens,
    }
    if direct_answer:
        result["final_response"] = direct_answer
    return result


# ── 7. policy_and_approval_gate ───────────────────────────────────────────────


async def policy_and_approval_gate_node(state: AgentState) -> dict[str, Any]:
    """
    Classify the planned action's risk and require human confirmation when it mutates
    external state.

    When confirmation is needed the node calls ``interrupt()``, which suspends the graph
    here and persists its state through the checkpointer. Resuming with a decision
    re-enters this node, ``interrupt()`` returns that decision, and execution continues
    into the rest of the plan rather than being dispatched out-of-band.
    """
    pending = state.get("pending_tool_call")
    if not pending:
        return {"policy_decision": "NO_OP"}

    registry = get_tool_registry()
    tool = registry.get_tool(pending["name"])

    # Determine risk level & policy
    risk = getattr(tool, "risk_level", RiskLevel.READ_ONLY)
    if isinstance(risk, str):
        try:
            risk = RiskLevel(risk)
        except Exception:
            risk = RiskLevel.READ_ONLY

    policy = getattr(tool, "approval_policy", ApprovalPolicy.AUTO_APPROVED)

    # Track why approval is needed so the user sees an accurate reason, not a risk tier
    # that may not be the trigger.
    approval_reasons: List[str] = []
    if risk in [RiskLevel.EXTERNAL_WRITE, RiskLevel.FINANCIAL, RiskLevel.DESTRUCTIVE]:
        approval_reasons.append(f"risk tier {risk.value}")
    if policy == ApprovalPolicy.REQUIRE_APPROVAL or getattr(tool, "approval_required", False):
        approval_reasons.append("tool policy requires approval")
    if getattr(tool, "is_mutating", False):
        approval_reasons.append(f"{getattr(tool, 'method', 'POST')} mutates external state")
    requires_approval = bool(approval_reasons)

    timeline = list(state.get("timeline_events", []))

    if not requires_approval:
        timeline.append(
            {
                "stage": "policy_and_approval_gate",
                "status": "auto_approved",
                "timestamp": time.time(),
                "details": f"Action {pending['name']} auto-approved ({risk.value}).",
            }
        )
        return {"policy_decision": "AUTO_APPROVED", "timeline_events": timeline}

    # A decision already recorded for this step (for example a pre-approved retry).
    if state.get("approval_status") == "approved":
        timeline.append(
            {
                "stage": "policy_and_approval_gate",
                "status": "approved",
                "timestamp": time.time(),
                "details": f"Action {pending['name']} verified and approved.",
            }
        )
        return {"policy_decision": "APPROVED", "timeline_events": timeline}

    # Deterministic id: this node re-executes on resume, so a random id would create a
    # second approval row for the same step.
    step_index = state.get("iteration_count", 0)
    req_id = f"appr-{state.get('request_id', 'run')}-{step_index}"

    approval_payload = {
        "approval_request_id": req_id,
        "tool_name": pending["name"],
        "risk_level": risk.value if hasattr(risk, "value") else str(risk),
        "arguments": pending.get("arguments", {}),
        "reasons": approval_reasons,
        "warning": f"Approval required: {'; '.join(approval_reasons)}. Confirm to proceed.",
    }

    await _record_approval_request(state, req_id, pending["name"], approval_payload)

    # Suspend the graph. On the first pass this raises GraphInterrupt and the caller sees
    # the payload; on resume it returns the decision that was supplied.
    decision = interrupt(approval_payload)

    action = "approve"
    reason = None
    if isinstance(decision, dict):
        action = str(decision.get("action", "approve")).lower()
        reason = decision.get("reason")
    elif isinstance(decision, str):
        action = decision.lower()
    elif decision is False:
        action = "reject"

    await _settle_approval_request(req_id, action, reason)

    if action in ("reject", "rejected", "cancel", "deny"):
        timeline.append(
            {
                "stage": "policy_and_approval_gate",
                "status": "rejected",
                "timestamp": time.time(),
                "details": f"User rejected {pending['name']}." + (f" Reason: {reason}" if reason else ""),
                "metadata": approval_payload,
            }
        )
        return {
            "approval_status": "rejected",
            "approval_request_id": req_id,
            "pending_approval": None,
            "pending_tool_call": None,
            "policy_decision": "REJECTED",
            "timeline_events": timeline,
            "final_response": (
                f"\u274c Cancelled. I did not run `{pending['name']}`."
                + (f"{chr(10)}{chr(10)}Reason: {reason}" if reason else "")
            ),
        }

    timeline.append(
        {
            "stage": "policy_and_approval_gate",
            "status": "approved",
            "timestamp": time.time(),
            "details": f"User approved {pending['name']}; proceeding to execution.",
            "metadata": approval_payload,
        }
    )
    return {
        "approval_status": "approved",
        "approval_request_id": req_id,
        "pending_approval": None,
        "policy_decision": "APPROVED",
        "timeline_events": timeline,
    }


async def _record_approval_request(
    state: AgentState,
    approval_id: str,
    tool_name: str,
    payload: Dict[str, Any],
) -> None:
    """Persist a pending approval, tolerating the node re-running on resume."""
    try:
        async with async_session_factory() as s:
            existing = (
                await s.execute(
                    select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
                )
            ).scalar_one_or_none()
            if existing:
                return
            s.add(
                ApprovalRequest(
                    approval_id=approval_id,
                    run_id=state.get("request_id", ""),
                    user_id=state.get("user_id", "default_user"),
                    tool_name=tool_name,
                    input_args=payload,
                    status="pending",
                )
            )
            await s.commit()
    except Exception as exc:
        logger.warning("Could not persist approval request %s: %s", approval_id, exc)


async def _settle_approval_request(approval_id: str, action: str, reason: Optional[str]) -> None:
    """Mark a persisted approval row with the decision the user made."""
    try:
        async with async_session_factory() as s:
            row = (
                await s.execute(
                    select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
                )
            ).scalar_one_or_none()
            if row and row.status == "pending":
                row.status = "approved" if action.startswith("appro") else "rejected"
                row.reason = reason
                await s.commit()
    except Exception as exc:
        logger.warning("Could not settle approval request %s: %s", approval_id, exc)


# ── 8. execute_tool ───────────────────────────────────────────────────────────


async def execute_tool_node(state: AgentState) -> dict[str, Any]:
    """Execute the pending tool call through the sandboxed ExecutionEngine and record relational audit logs."""
    pending = state.get("pending_tool_call")
    if not pending:
        return {}

    engine = get_execution_engine()
    registry = get_tool_registry()
    tool = registry.get_tool(pending["name"])

    if not tool:
        # Construct synthetic registered tool on the fly if needed
        from app.schemas.tool import ParameterDefinition
        tool = RegisteredTool(
            name=pending["name"],
            display_name=pending["name"],
            description="Dynamically executed tool",
            method="POST",
            path=f"/tools/{pending['name']}",
            parameters=[ParameterDefinition(name=k, type="string", required=False) for k in pending.get("arguments", {})],
        )

    res = await engine.execute_tool(
        tool=tool,
        arguments=pending.get("arguments", {}),
        session_id=state.get("session_id", "default_session"),
        request_id=state.get("request_id"),
        user_id=state.get("user_id", "system"),
        user_role=state.get("user_role", "user"),
    )

    exec_log = list(state.get("execution_log", []))
    exec_log.append(res.model_dump() if hasattr(res, "model_dump") else res.__dict__)

    trace = list(state.get("execution_trace", []))
    trace.append(
        {
            "step": "execution",
            "tool_name": tool.name,
            "arguments": pending.get("arguments", {}),
            "status": res.status.value if hasattr(res.status, "value") else str(res.status),
            "latency_ms": res.latency_ms,
            "response": res.response_payload,
        }
    )

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "execute_tool",
            "status": "completed",
            "timestamp": time.time(),
            "details": f"Executed {tool.name} in {res.latency_ms}ms ({res.status.value}).",
            "metadata": {
                "tool": tool.name,
                "latency_ms": res.latency_ms,
                "status": res.status.value if hasattr(res.status, "value") else str(res.status),
            },
        }
    )

    return {
        "execution_log": exec_log,
        "execution_trace": trace,
        "timeline_events": timeline,
        "last_result": res.response_payload,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }


# ── 9. evaluate_result ────────────────────────────────────────────────────────


async def evaluate_result_node(state: AgentState) -> dict[str, Any]:
    """
    Decide whether the goal is met or another step is required.

    With the reasoning layer available the decision is delegated to the model: control
    returns to ``plan_and_select``, which sees the results of every completed step and
    either selects the next tool or answers. That is what makes genuine multi-step work
    (fetch a report, then email it) possible without hardcoding the chain.

    Without it, the deterministic chain below handles the two flows the rule planner knows.
    """
    last_res = state.get("last_result", {}) or {}
    pending = state.get("pending_tool_call") or {}
    tool_name = pending.get("name", "")
    iteration = state.get("iteration_count", 1)
    max_iter = state.get("max_iterations", 10)
    budget_left = iteration < max_iter

    timeline = list(state.get("timeline_events", []))

    # ── Model-driven replanning ──────────────────────────────────────────
    if get_llm_service().available:
        last_status = None
        for step in reversed(state.get("execution_trace", [])):
            if step.get("step") == "execution":
                last_status = step.get("status")
                break
        # Always return to the planner while budget remains. The loop terminates when the
        # model stops calling tools, which also lets it react to a failed step by retrying
        # with corrected arguments or consulting the RAG tool about the error code.
        should_replan = budget_left

        timeline.append(
            {
                "stage": "evaluate_result",
                "status": "completed",
                "timestamp": time.time(),
                "details": (
                    "Returning to planner to decide the next step."
                    if should_replan
                    else "Iteration budget exhausted; composing answer."
                ),
                "metadata": {"iteration": iteration, "last_status": last_status},
            }
        )
        return {
            "next_action": "replan" if should_replan else "finish",
            "has_next_step": should_replan,
            "pending_tool_call": None,
            "timeline_events": timeline,
        }

    # ── Deterministic chaining fallback ──────────────────────────────────
    has_next = False
    next_step = None
    plan = list(state.get("plan", []))
    step_idx = state.get("current_step_index", 0)

    # 1. Multi-step plan transition (e.g. Step 1: Reporting -> Step 2: Email Dispatch)
    if len(plan) > 1 and step_idx == 0 and budget_left:
        next_step_def = plan[1]
        has_next = True
        next_tool = next_step_def["tool"]
        next_args = dict(next_step_def.get("arguments", {}))

        # Output-to-input mapping: pass output from Step 1 into Step 2
        if next_tool == "send_email" and isinstance(last_res, dict):
            next_args["body"] = _format_report_email(last_res)
            recipient = next_args.get("to") or state.get("dependency_mapping", {}).get("email")
            if recipient:
                next_args["to"] = recipient

        next_step = {"name": next_tool, "arguments": next_args}
        step_idx = 1

    # 2. Invoicing multi-step fallback: a created draft must be sent to be payable.
    elif "create" in tool_name and "invoice" in tool_name and "send" in state.get("user_prompt", "").lower():
        inv_id = last_res.get("id") or last_res.get("invoice_id")
        recipient = last_res.get("recipient_email") or state.get("dependency_mapping", {}).get("email")
        if inv_id and recipient and budget_left:
            has_next = True
            amount_obj = last_res.get("amount") if isinstance(last_res.get("amount"), dict) else {}
            next_step = {
                "name": "send_invoice",
                "arguments": {
                    "invoice_id": inv_id,
                    "recipient_email": recipient,
                    "amount": amount_obj.get("value") or state.get("dependency_mapping", {}).get("amount"),
                    "currency_code": amount_obj.get("currency_code", "USD"),
                },
            }

    timeline.append(
        {
            "stage": "evaluate_result",
            "status": "completed",
            "timestamp": time.time(),
            "details": "Scheduling next step." if has_next else "Goal achieved.",
        }
    )

    return {
        "next_action": "execute_next" if has_next else "finish",
        "has_next_step": has_next,
        "current_step_index": step_idx,
        "pending_tool_call": next_step if has_next else None,
        "timeline_events": timeline,
    }


def _format_report_email(report: dict[str, Any]) -> str:
    """Format a transaction report payload as an email body, using only returned figures."""
    sales = report.get("total_sales_volume") or {}
    fees = report.get("total_fee") or {}
    sales_val = sales.get("value") if isinstance(sales, dict) else None
    fee_val = fees.get("value") if isinstance(fees, dict) else None
    currency = (sales.get("currency_code") if isinstance(sales, dict) else None) or "USD"
    items = report.get("total_items")

    lines = ["Sales Volume & Financial Report", "=" * 34]
    if sales_val is not None:
        lines.append(f"Total Sales Volume:  {sales_val} {currency}")
    if items is not None:
        lines.append(f"Total Transactions:  {items}")
    if fee_val is not None:
        lines.append(f"Processing Fees:     {fee_val} {currency}")
    try:
        net = float(str(sales_val).replace(",", "")) - float(str(fee_val).replace(",", ""))
        lines.append(f"Net Settled Revenue: {net:.2f} {currency}")
    except (TypeError, ValueError):
        pass
    lines += ["=" * 34, "", "Generated by the Datazoic Agentic Platform."]
    return "\n".join(lines)


# ── 10. compose_final_answer ──────────────────────────────────────────────────


def _render_template_answer(state: AgentState) -> str:
    """
    Render the user-facing answer from per-intent templates.

    Fallback for when Gemini synthesis is unavailable. Deterministic and offline-safe,
    but only covers the intents encoded below.
    """
    existing_response = state.get("final_response")
    last_res = state.get("last_result")
    pending = state.get("pending_tool_call")
    intent = state.get("intent", "GENERAL_QA")

    # If already set by clarification or approval gate, keep it
    if existing_response and not last_res:
        response_text = existing_response
    elif last_res:
        # Synthesize from tool execution
        if intent == "CALCULATION":
            formatted = last_res.get("formatted") or last_res.get("result")
            response_text = f"🧮 **Calculation Result:**\n\n`{last_res.get('expression')}` = **{formatted}**"
        elif intent == "TIME":
            response_text = (
                f"🕒 **Current Time ({last_res.get('timezone')}):**\n\n"
                f"- **Date:** {last_res.get('date')} ({last_res.get('day_of_week')})\n"
                f"- **Time:** {last_res.get('time')}\n"
                f"- **ISO:** `{last_res.get('iso')}`"
            )
        elif intent == "COMMUNICATION":
            if "draft_id" in last_res:
                response_text = f"✉️ **Email Draft Created:**\n\n- **Draft ID:** `{last_res.get('draft_id')}`\n- **To:** {last_res.get('to')}\n- **Subject:** {last_res.get('subject')}\n\n*The draft has been saved and is ready for review before sending.*"
            else:
                is_real = last_res.get("real_smtp_delivery", False)
                header = "📬 **Real Email Delivered via Google SMTP!**" if is_real else "📋 **Email Dispatched (Simulation Mode)**"
                response_text = (
                    f"{header}\n\n"
                    f"- **Recipient:** {last_res.get('to')}\n"
                    f"- **Subject:** {last_res.get('subject')}\n"
                    f"- **Message ID:** `{last_res.get('message_id')}`\n"
                    f"- **Delivery Status:** {last_res.get('message')}"
                )
        elif intent == "KNOWLEDGE_RAG":
            results = last_res.get("results", [])
            if results:
                top_match = results[0]
                response_text = (
                    f"📖 **PayPal Documentation Answer:**\n\n"
                    f"{top_match.get('snippet', '')}\n\n"
                    f"*(Source: {top_match.get('title', 'PayPal Developer Docs')})*"
                )
            else:
                response_text = (
                    "📖 I could not find anything in the indexed knowledge base for that question. "
                    "An administrator can add the relevant document under **Tools & Sources → "
                    "Documents & Knowledge Base**."
                )
        elif intent == "SYSTEM_INTROSPECTION":
            scope = last_res.get("scope")
            if scope == "capabilities":
                matched = last_res.get("matched_tools", [])
                total = last_res.get("total_registered_tools", 0)
                if matched:
                    rows = "\n".join(
                        f"- `{t.get('name')}` ({t.get('method')}) \u2014 {t.get('description', '')[:110]}"
                        for t in matched
                    )
                    response_text = (
                        f"\U0001f527 **Matching tools ({len(matched)} of {total} registered):**"
                        f"\n\n{rows}"
                    )
                else:
                    response_text = (
                        f"\U0001f527 None of the {total} registered tools matched that description."
                    )
            else:
                logs = last_res.get("logs", [])
                if logs:
                    rows = "\n".join(
                        f"- `{l.get('tool_name')}` \u2014 **{str(l.get('status', '')).upper()}** "
                        f"({l.get('latency_ms', 0):.0f} ms) at {str(l.get('created_at', ''))[:19]}"
                        for l in logs
                    )
                    response_text = f"\U0001f4dc **Recent executions:**\n\n{rows}"
                else:
                    response_text = "\U0001f4dc No execution history recorded yet for your account."
        elif intent == "REPORTING":
            # Report only what the tool returned; never substitute placeholder figures.
            figures: Dict[str, Any] = {}
            for trace_step in state.get("execution_trace", []):
                payload = trace_step.get("response") or {}
                if isinstance(payload, dict) and "total_sales_volume" in payload:
                    figures = payload
                    break

            sales = figures.get("total_sales_volume") or {}
            fees = figures.get("total_fee") or {}
            sales_val = sales.get("value") if isinstance(sales, dict) else None
            fee_val = fees.get("value") if isinstance(fees, dict) else None
            currency = (sales.get("currency_code") if isinstance(sales, dict) else None) or "USD"
            items_cnt = figures.get("total_items")

            if sales_val is None:
                response_text = (
                    "\U0001f4ca The reporting tool returned no sales figures for that period."
                )
            else:
                rows = [f"- **Total Sales Volume:** **{sales_val} {currency}**"]
                if items_cnt is not None:
                    rows.append(f"- **Transactions Processed:** {items_cnt}")
                if fee_val is not None:
                    rows.append(f"- **Processing Fees:** {fee_val} {currency}")
                    try:
                        net = float(str(sales_val).replace(",", "")) - float(
                            str(fee_val).replace(",", "")
                        )
                        rows.append(f"- **Net Settled Revenue:** **{net:.2f} {currency}**")
                    except (TypeError, ValueError):
                        pass

                recipient = None
                for trace_step in state.get("execution_trace", []):
                    if "email" in str(trace_step.get("tool_name", "")):
                        recipient = (trace_step.get("response") or {}).get("to")
                        break

                body = "\n".join(rows)
                response_text = f"\U0001f4ca **Sales Volume Report:**\n\n{body}"
                if recipient:
                    response_text += f"\n\n\U0001f4ec Emailed to **`{recipient}`**."
        elif "invoice" in str(last_res).lower():
            inv_id = last_res.get("id") or last_res.get("invoice_id", "INV-SAMPLE")
            status = last_res.get("status", "CREATED")
            amount = last_res.get("amount", {}).get("value") or state.get("dependency_mapping", {}).get("amount", "100.00")
            recipient = last_res.get("recipient_email") or state.get("dependency_mapping", {}).get("email") or "the recipient"
            is_real = last_res.get("real_smtp_delivery", False)
            if status == "SENT" or is_real:
                delivery_header = "📬 **Invoice Issued & Real Email Dispatched via Google SMTP!**" if is_real else "📋 **Invoice Issued & Dispatched**"
                delivery_note = (
                    f"Outbound email delivered to `{recipient}` via SMTP."
                    if is_real
                    else f"Invoice record dispatched to `{recipient}`."
                )
                response_text = (
                    f"{delivery_header}\n\n"
                    f"- **Invoice ID:** `{inv_id}`\n"
                    f"- **Recipient:** {recipient}\n"
                    f"- **Status:** `{status}`\n"
                    f"- **Amount:** ${amount} USD\n"
                    f"- **Email Delivery:** {delivery_note}"
                )
            else:
                response_text = (
                    f"📄 **Invoice Operation Successful:**\n\n"
                    f"- **Invoice ID:** `{inv_id}`\n"
                    f"- **Recipient:** {recipient}\n"
                    f"- **Status:** `{status}`\n"
                    f"- **Amount:** ${amount} USD\n\n"
                    f"💡 *Note:* This invoice has been created in **DRAFT** status. In the PayPal lifecycle, draft invoices are staged and not yet dispatched. To dispatch it and deliver a real email to `{recipient}`, send: **`Send invoice {inv_id} to {recipient}`** or **`Send invoice for ${amount} to {recipient}`**."
                )
        else:
            response_text = f"✅ **Operation Complete:**\n\n```json\n{json.dumps(last_res, indent=2)}\n```"
    else:
        response_text = "I have reviewed your request. Please specify which action or tool you would like me to invoke."

    return response_text


async def compose_final_answer_node(state: AgentState) -> dict[str, Any]:
    """
    Write the user-facing answer, grounded in the tool results, and record token usage.

    Primary path: Gemini synthesis over the execution trace, which also completes the RAG
    story (retrieved chunks are summarised and cited rather than pasted verbatim).

    Fallback path: per-intent templates, used when the model is unavailable.
    """
    settings = get_settings()
    tokens = dict(state.get("token_usage") or _EMPTY_TOKENS)
    executed = _completed_step_summaries(state)
    pending_approval = state.get("approval_status") == "pending"
    synthesis_mode = "template"

    response_text: Optional[str] = None
    llm = get_llm_service()

    # An approval pause or a clarification prompt is a control message, not an answer to
    # synthesise: it must reach the user verbatim so the UI can render the approval card.
    control_message = state.get("final_response") if (pending_approval or state.get("requires_clarification")) else None

    if control_message:
        response_text = control_message
    else:
        synthesized = await llm.synthesize_answer(
            user_prompt=state.get("user_prompt", ""),
            execution_results=executed,
            history=state.get("messages", []),
        )
        if synthesized is not None:
            response_text, usage = synthesized
            tokens = _accumulate_tokens(tokens, usage.as_dict())
            synthesis_mode = "gemini"

    if not response_text:
        response_text = _render_template_answer(state)

    # Record the real token usage accumulated across this turn's model calls.
    if tokens.get("total_tokens", 0) > 0:
        await get_token_usage_service().record_token_usage(
            request_id=state.get("request_id", ""),
            user_id=state.get("user_id", "default_user"),
            input_tokens=tokens.get("input_tokens", 0),
            output_tokens=tokens.get("output_tokens", 0),
            model=settings.gemini_model,
        )

    timeline = list(state.get("timeline_events", []))
    timeline.append(
        {
            "stage": "compose_final_answer",
            "status": "completed",
            "timestamp": time.time(),
            "details": (
                f"Answer composed via {synthesis_mode}; "
                f"{tokens.get('total_tokens', 0)} model tokens consumed this turn."
            ),
            "metadata": {**tokens, "synthesis": synthesis_mode},
        }
    )

    return {
        "final_response": response_text,
        "token_usage": tokens,
        "timeline_events": timeline,
    }
