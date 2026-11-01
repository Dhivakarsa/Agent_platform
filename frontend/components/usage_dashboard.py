"""
Personal Usage & Activity Dashboard component (Screen 3 - Normal User View).
"""

from __future__ import annotations

import streamlit as st
from frontend.services.backend_client import BackendClient


def render_user_usage(client: BackendClient) -> None:
    """Render personal metrics and execution history for the logged-in normal user."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})

    st.subheader("📊 Personal Usage & Activity Dashboard")
    st.caption(f"Activity, model token consumption, and execution audit history for **{user.get('email')}**.")

    with st.spinner("Fetching personal usage statistics..."):
        try:
            summary = client.get_my_usage(token)
        except Exception as exc:
            st.error(f"Error loading personal usage: {exc}")
            return

    # Metric KPI cards
    col_k1, col_k2, col_k3, col_k4 = st.columns(4)
    with col_k1:
        st.metric("Total Requests", summary.get("total_requests", 0))
    with col_k2:
        st.metric("Successful Tasks", summary.get("successful_requests", 0))
    with col_k3:
        st.metric("Failed / Rejected", summary.get("failed_requests", 0))
    with col_k4:
        st.metric("Total Gemini Tokens", f"{summary.get('total_tokens', 0):,}")

    st.divider()

    # Token breakdown
    col_tb1, col_tb2 = st.columns(2)
    with col_tb1:
        st.markdown("#### 🧠 Gemini Token Breakdown")
        st.write(f"• **Prompt / Input Tokens:** `{summary.get('total_input_tokens', 0):,}`")
        st.write(f"• **Generated / Output Tokens:** `{summary.get('total_output_tokens', 0):,}`")
        st.write(f"• **Total Model Tokens:** `{summary.get('total_tokens', 0):,}`")
        st.caption("Note: Tokens track Gemini LLM reasoning. External PayPal Sandbox API calls do not consume model tokens.")

    with col_tb2:
        st.markdown("#### ⚡ Execution Efficiency")
        tot = summary.get("total_requests", 0)
        success_rate = (summary.get("successful_requests", 0) / tot * 100) if tot > 0 else 100.0
        st.write(f"• **Task Success Rate:** `{success_rate:.1f}%`")
        st.write(f"• **Average Tokens per Turn:** `{int(summary.get('total_tokens', 0) / (tot or 1)):,}` tokens")

    st.divider()

    # Execution History
    st.subheader("📋 Personal Task Execution History")
    executions = summary.get("recent_executions", [])

    if not executions:
        st.info("No tasks executed yet. Start a conversation in the Chat screen!")
        return

    # Filters
    col_filt1, col_filt2 = st.columns(2)
    with col_filt1:
        status_filter = st.selectbox("Filter Status", ["All", "completed", "failed", "waiting_approval"])
    with col_filt2:
        search_query = st.text_input("Filter by Query Keyword", placeholder="e.g. invoice, report...")

    filtered_execs = executions
    if status_filter != "All":
        filtered_execs = [e for e in filtered_execs if e.get("status") == status_filter]
    if search_query:
        filtered_execs = [e for e in filtered_execs if search_query.lower() in e.get("query", "").lower()]

    # Table view
    for e in filtered_execs:
        with st.container(border=True):
            col_e1, col_e2, col_e3 = st.columns([3, 1.5, 1])
            with col_e1:
                st.markdown(f"**Query:** {e.get('query')}")
                st.caption(f"Request ID: `{e.get('request_id')}` | Timestamp: {e.get('created_at')[:19]}")
                tools = e.get("tools_used", [])
                if tools:
                    st.write("Tools:", ", ".join(f"`{t}`" for t in tools))
            with col_e2:
                st.write(f"Duration: `{e.get('duration_ms', 0):.0f} ms`")
            with col_e3:
                st_val = e.get("status", "completed").upper()
                badge_bg = "#198754" if st_val in ["COMPLETED", "SUCCESS"] else "#dc3545"
                st.markdown(
                    f"<span style='background-color: {badge_bg}; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;'>{st_val}</span>",
                    unsafe_allow_html=True,
                )

