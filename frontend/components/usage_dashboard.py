"""Personal Usage & Activity Dashboard component (Screen 3 - Normal User View)."""

from __future__ import annotations

import streamlit as st

from frontend.components.activity_table import render_task_token_table
from frontend.services.backend_client import BackendClient


def render_user_usage(client: BackendClient) -> None:
    """Render personal metrics and per-task token usage for the logged-in user."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})

    st.subheader("📊 Personal Usage & Activity")
    st.caption(f"Activity and model token consumption for **{user.get('email')}**.")

    with st.spinner("Fetching personal usage statistics..."):
        try:
            summary = client.get_my_usage(token)
            activity = client.get_my_activity(token)
        except Exception as exc:
            st.error(f"Error loading personal usage: {exc}")
            return

    col_k1, col_k2, col_k3, col_k4 = st.columns(4)
    with col_k1:
        st.metric("Total Requests", summary.get("total_requests", 0))
    with col_k2:
        st.metric("Successful Tasks", summary.get("successful_requests", 0))
    with col_k3:
        st.metric("Failed / Rejected", summary.get("failed_requests", 0))
    with col_k4:
        st.metric("Total Model Tokens", f"{summary.get('total_tokens', 0):,}")

    st.divider()

    col_tb1, col_tb2 = st.columns(2)
    with col_tb1:
        st.markdown("#### 🧠 Token Breakdown")
        st.write(f"• **Prompt / Input Tokens:** `{summary.get('total_input_tokens', 0):,}`")
        st.write(f"• **Generated / Output Tokens:** `{summary.get('total_output_tokens', 0):,}`")
        st.write(f"• **Total Model Tokens:** `{summary.get('total_tokens', 0):,}`")
        st.caption(
            "Tokens are the counts the model SDK reported for this account. External API "
            "calls do not consume model tokens."
        )

    with col_tb2:
        st.markdown("#### ⚡ Execution Efficiency")
        total = summary.get("total_requests", 0)
        rate = (summary.get("successful_requests", 0) / total * 100) if total else 0.0
        st.write(f"• **Task Success Rate:** `{rate:.1f}%`")
        avg = int(summary.get("total_tokens", 0) / total) if total else 0
        st.write(f"• **Average Tokens per Task:** `{avg:,}`")

    st.divider()
    st.subheader("📋 My Tasks & Token Usage")

    if not activity:
        st.info("No tasks executed yet. Start a conversation in the Chat screen!")
        return

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        statuses = ["All"] + sorted({str(a.get("status", "")) for a in activity if a.get("status")})
        status_filter = st.selectbox("Filter Status", statuses)
    with col_f2:
        search = st.text_input("Filter by Keyword", placeholder="e.g. invoice, report...")

    filtered = activity
    if status_filter != "All":
        filtered = [a for a in filtered if a.get("status") == status_filter]
    if search:
        filtered = [a for a in filtered if search.lower() in (a.get("task") or "").lower()]

    render_task_token_table(filtered, key_prefix="me")
