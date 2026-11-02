"""
Chat Interface component (Screen 1).

Shows the real pipeline stages the backend reported for a turn, pauses on actions that
need confirmation, and resumes the suspended run when the user decides.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.services.backend_client import BackendClient

_STAGE_LABELS = {
    "normalize_input": "Request received",
    "analyze_intent": "Identifying intent",
    "select_registries": "Scoping tool registries",
    "retrieve_tools": "Finding candidate tools",
    "validate_candidates": "Validating candidates",
    "plan_and_select": "Choosing tool & arguments",
    "policy_and_approval_gate": "Approval check",
    "execute_tool": "Executing",
    "evaluate_result": "Evaluating result",
    "compose_final_answer": "Composing answer",
}

_STATUS_BADGE = {
    "completed": "🟢",
    "approved": "🟢",
    "auto_approved": "🟢",
    "paused_for_approval": "🟡",
    "clarification_needed": "🟡",
    "rejected": "🔴",
    "failed": "🔴",
}


def _badge(status: str) -> str:
    return _STATUS_BADGE.get(str(status).lower(), "🔵")


def _render_timeline(events: list[dict[str, Any]]) -> None:
    """Render the stages the backend actually executed for this turn."""
    if not events:
        return
    with st.expander(f"⏱️ Pipeline stages ({len(events)} executed)"):
        for idx, event in enumerate(events, start=1):
            stage = event.get("stage", "stage")
            status = event.get("status", "completed")
            label = _STAGE_LABELS.get(stage, stage)
            st.markdown(
                f"{_badge(status)} **{idx}. {label}** `{stage}` — {event.get('details', '')}"
            )
            meta = event.get("metadata")
            if meta:
                st.caption(f"`{meta}`")


def _render_assistant_extras(msg: dict[str, Any]) -> None:
    """Tools, tokens, stages and trace for one assistant turn."""
    if msg.get("tools"):
        st.caption(f"🔧 **Tools executed:** {', '.join(msg['tools'])}")

    usage = msg.get("token_usage") or {}
    if usage.get("total_tokens"):
        st.caption(
            f"🪙 **Model tokens:** {usage.get('total_tokens', 0):,} "
            f"(prompt {usage.get('input_tokens', 0):,}, output {usage.get('output_tokens', 0):,})"
        )

    _render_timeline(msg.get("timeline_events") or [])

    trace = msg.get("trace") or []
    if trace:
        with st.expander(f"🔍 Execution trace ({len(trace)} step(s))"):
            for idx, step in enumerate(trace, start=1):
                st.markdown(f"**Step {idx}: `{step.get('tool_name', step.get('step', ''))}`**")
                cols = st.columns(2)
                with cols[0]:
                    st.caption(f"Status: `{step.get('status', '')}` · {step.get('latency_ms', 0)} ms")
                    st.markdown("Arguments")
                    st.json(step.get("arguments") or {})
                with cols[1]:
                    st.markdown("Response")
                    st.json(step.get("response") or {})


def _store_turn(response: dict[str, Any]) -> None:
    """Append an assistant turn from a chat or approval response."""
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": response.get("response", ""),
            "tools": response.get("tools_used", []),
            "trace": response.get("execution_trace", []),
            "timeline_events": response.get("timeline_events", []),
            "token_usage": response.get("token_usage", {}),
        }
    )


def _capture_pending(response: dict[str, Any]) -> None:
    """Record a pending approval so the decision buttons appear, or clear it."""
    pending = response.get("pending_approval")
    if response.get("status") == "waiting_approval" and pending:
        st.session_state.pending_approval_run = {
            "run_id": response["run_id"],
            "tool_name": pending.get("tool_name"),
            "risk_level": pending.get("risk_level", ""),
            "reasons": pending.get("reasons") or [],
            "input_arguments": pending.get("arguments", {}),
        }
    else:
        st.session_state.pending_approval_run = None


def render_chat(client: BackendClient) -> None:
    """Render the chat interface with live pipeline stages and the approval workflow."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})

    st.header("💬 Agent Chat Interface")
    st.caption(
        "Ask in natural language. The agent narrows hundreds of APIs to a shortlist, "
        "picks one, and pauses before anything that changes external state."
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = f"conv-{user.get('user_id', 'anon')[:6]}"
    if "pending_approval_run" not in st.session_state:
        st.session_state.pending_approval_run = None

    col_ctrl1, _ = st.columns([1, 4])
    with col_ctrl1:
        if st.button("➕ New Conversation"):
            st.session_state.messages = []
            st.session_state.conversation_id = (
                f"conv-{user.get('user_id', 'anon')[:6]}-{st.session_state.get('conv_seq', 0) + 1}"
            )
            st.session_state.conv_seq = st.session_state.get("conv_seq", 0) + 1
            st.session_state.pending_approval_run = None
            st.rerun()

    st.divider()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                _render_assistant_extras(msg)

    # ── Pending approval ────────────────────────────────────────────────
    if st.session_state.pending_approval_run:
        app_data = st.session_state.pending_approval_run
        reasons = "; ".join(app_data.get("reasons") or ["policy requires approval"])
        st.warning(
            f"⚠️ **Confirmation required** before running `{app_data['tool_name']}`.\n\n"
            f"Reason: {reasons}"
        )
        st.markdown("**Arguments that will be sent:**")
        st.json(app_data.get("input_arguments", {}))

        col_app1, col_app2 = st.columns(2)
        with col_app1:
            if st.button("✅ Approve", type="primary", use_container_width=True):
                with st.spinner("Resuming the run..."):
                    try:
                        res = client.submit_approval(token, app_data["run_id"], action="approve")
                        _store_turn(res)
                        _capture_pending(res)
                    except Exception as exc:
                        st.session_state.pending_approval_run = None
                        st.session_state.messages.append(
                            {"role": "assistant", "content": f"⚠️ Could not complete the action: {exc}"}
                        )
                st.rerun()
        with col_app2:
            if st.button("❌ Reject", use_container_width=True):
                with st.spinner("Cancelling..."):
                    try:
                        res = client.submit_approval(
                            token, app_data["run_id"], action="reject", reason="Rejected by user"
                        )
                        _store_turn(res)
                    except Exception as exc:
                        st.session_state.messages.append(
                            {"role": "assistant", "content": f"⚠️ Could not cancel: {exc}"}
                        )
                    st.session_state.pending_approval_run = None
                st.rerun()

    # ── New input ───────────────────────────────────────────────────────
    active_prompt = st.session_state.get("active_chat_prompt", "")
    user_query = st.chat_input("Enter instruction (e.g. 'Send an invoice for $50 to client@example.com')...")

    if active_prompt and not user_query:
        user_query = active_prompt
        st.session_state.active_chat_prompt = ""

    if not user_query:
        return

    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        progress = st.status("Running the agent pipeline...", expanded=True)
        try:
            res = client.send_chat(
                token=token,
                query=user_query,
                conversation_id=st.session_state.conversation_id,
            )
        except Exception as exc:
            progress.update(label="Execution failed", state="error", expanded=True)
            st.error(f"Error executing agent workflow: {exc}")
            st.session_state.messages.append(
                {"role": "assistant", "content": f"⚠️ The request failed: {exc}"}
            )
            return

        # Report the stages the backend actually ran, rather than a fixed script.
        with progress:
            for event in res.get("timeline_events") or []:
                stage = event.get("stage", "")
                label = _STAGE_LABELS.get(stage, stage)
                st.write(f"{_badge(event.get('status', ''))} {label} — {event.get('details', '')}")

        waiting = res.get("status") == "waiting_approval"
        progress.update(
            label="Paused for approval" if waiting else "Pipeline complete",
            state="running" if waiting else "complete",
            expanded=waiting,
        )

        _store_turn(res)
        _capture_pending(res)

    st.rerun()
