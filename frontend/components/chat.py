"""
Chat Interface component (Screen 1).
"""

from __future__ import annotations

import streamlit as st
from frontend.services.backend_client import BackendClient


def render_chat(client: BackendClient) -> None:
    """Render the primary Chat interface with multi-step progress and approval workflow."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})

    st.header("💬 Agent Chat Interface")
    st.caption("Interact with 50+ PayPal APIs, RAG Documentation, and System Search in natural language.")

    # Initialize messages and conversation ID in session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = f"conv-{user.get('user_id', 'anon')[:6]}"
    if "pending_approval_run" not in st.session_state:
        st.session_state.pending_approval_run = None

    # Header controls
    col_ctrl1, col_ctrl2 = st.columns([1, 4])
    with col_ctrl1:
        if st.button("➕ New Conversation"):
            st.session_state.messages = []
            st.session_state.conversation_id = f"conv-{user.get('user_id', 'anon')[:6]}-{len(st.session_state.messages)}"
            st.session_state.pending_approval_run = None
            st.rerun()

    st.divider()

    # Display message history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

            if "tools" in msg and msg["tools"]:
                st.caption(f"🔧 **Tools Executed:** {', '.join(msg['tools'])}")

            if "token_usage" in msg and msg["token_usage"]:
                tu = msg["token_usage"]
                st.caption(f"🪙 **Tokens Accounted:** {tu.get('total_tokens', 0)} (Prompt: {tu.get('input_tokens', 0)}, Completion: {tu.get('output_tokens', 0)})")

            if "timeline_events" in msg and msg["timeline_events"]:
                with st.expander("⏱️ Task Timeline (10-Stage Pipeline Observability)"):
                    for idx, event in enumerate(msg["timeline_events"]):
                        stage = event.get("stage", "stage")
                        status = event.get("status", "completed")
                        details = event.get("details", "")
                        badge = "🟢" if status in ["completed", "auto_approved", "approved"] else "🟡" if "approval" in status or "clarification" in status else "🔵"
                        st.markdown(f"{badge} **Stage {idx+1}: `{stage}`** [{status}] — {details}")
                        if "metadata" in event and event["metadata"]:
                            st.caption(f"Payload: `{event['metadata']}`")

            if "trace" in msg and msg["trace"]:
                with st.expander("🔍 View Multi-Step Execution Trace"):
                    for idx, step in enumerate(msg["trace"]):
                        st.markdown(f"**Step {idx+1}: {step.get('step', '').upper()}**")
                        if "tool_name" in step:
                            st.write(f"Tool: `{step['tool_name']}` ({step.get('latency_ms', 0)}ms)")
                        if "arguments" in step:
                            st.write("Arguments:", step["arguments"])
                        if "response" in step:
                            st.code(str(step["response"]))

    # If an approval is currently pending
    if st.session_state.pending_approval_run:
        app_data = st.session_state.pending_approval_run
        st.warning(f"⚠️ **Action Requires Confirmation:** Tool `{app_data['tool_name']}` requires approval before execution.")
        st.json(app_data.get("input_arguments", {}))

        col_app1, col_app2 = st.columns(2)
        with col_app1:
            if st.button("✅ Approve Action", type="primary", use_container_width=True):
                with st.spinner("Executing approved action..."):
                    client.submit_approval(token, app_data["run_id"], action="approve")
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"✅ Approval granted for `{app_data['tool_name']}`. Execution completed successfully.",
                        "tools": [app_data["tool_name"]],
                    })
                    st.session_state.pending_approval_run = None
                    st.rerun()
        with col_app2:
            if st.button("❌ Reject Action", use_container_width=True):
                client.submit_approval(token, app_data["run_id"], action="reject", reason="Rejected by user")
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"❌ Action `{app_data['tool_name']}` was rejected.",
                })
                st.session_state.pending_approval_run = None
                st.rerun()

    # Chat input
    active_prompt = st.session_state.get("active_chat_prompt", "")
    user_query = st.chat_input("Enter instruction (e.g. 'Send an invoice for $50 to client@example.com')...")

    if active_prompt and not user_query:
        user_query = active_prompt
        st.session_state.active_chat_prompt = ""

    if user_query:
        # Append user message
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.markdown(user_query)

        # Multi-step execution progress placeholder
        with st.chat_message("assistant"):
            progress_box = st.status("Executing Multi-Step Agentic Workflow...", expanded=True)
            with progress_box:
                st.write("📥 Request received | Analyzing natural language intent...")
                st.write("🔍 Hybrid Retrieval: Querying Qdrant vectors + SQL metadata...")
                st.write("⚙️ Binding Top-K candidate tools and pinned Meta-Tools...")
                st.write("🧠 Gemini 2.5 Flash: Evaluating arguments & prerequisite dependencies...")

                try:
                    res = client.send_chat(
                        token=token,
                        query=user_query,
                        conversation_id=st.session_state.conversation_id,
                    )
                    progress_box.update(label="Workflow Complete!", state="complete", expanded=False)

                    # Display assistant response
                    st.markdown(res["response"])
                    if res["tools_used"]:
                        st.caption(f"🔧 **Tools Executed:** {', '.join(res['tools_used'])}")

                    # Check if run is paused for approval
                    if res.get("status") == "waiting_approval" and res.get("pending_approval"):
                        st.session_state.pending_approval_run = {
                            "run_id": res["run_id"],
                            "tool_name": res["pending_approval"].get("tool_name"),
                            "risk_level": res["pending_approval"].get("risk_level", "HIGH"),
                            "input_arguments": res["pending_approval"].get("arguments", {}),
                            "warning": res["pending_approval"].get("warning", ""),
                        }

                    # Add to message history
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": res["response"],
                        "tools": res.get("tools_used", []),
                        "trace": res.get("execution_trace", []),
                        "timeline_events": res.get("timeline_events", []),
                        "token_usage": res.get("token_usage", {}),
                    })
                    st.rerun()

                except Exception as exc:
                    progress_box.update(label="Execution Failed", state="error", expanded=True)
                    st.error(f"Error executing agent workflow: {exc}")

