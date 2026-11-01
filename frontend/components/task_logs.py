"""
Task Execution Logs component for both Normal Users and Administrators.
"""

from __future__ import annotations

import streamlit as st
from frontend.services.backend_client import BackendClient


def render_task_logs(client: BackendClient) -> None:
    """Render task execution audit logs with filtering, payload inspection, and latency metrics."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})
    is_admin = user.get("role") == "admin"

    title_text = "📜 System-Wide Task Execution Logs" if is_admin else "📜 Personal Task Execution Logs"
    sub_text = (
        "Inspect all tool executions across all platform users in real-time."
        if is_admin
        else f"Inspect all your tool invocations and API execution records ({user.get('email')})."
    )

    st.subheader(title_text)
    st.caption(sub_text)

    # Search and Filter Controls
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        search_kw = st.text_input("🔍 Search by Tool Name or Request ID", placeholder="e.g. invoice, refund, req-...")
    with col_f2:
        status_filter = st.selectbox(
            "Filter Status",
            ["All", "success", "error", "validation_error", "http_error", "internal_error"],
        )
    with col_f3:
        limit_val = st.selectbox("Show Last", [25, 50, 100, 200], index=1)

    # Fetch logs
    with st.spinner("Fetching task logs..."):
        try:
            data = client.get_execution_logs(
                token=token,
                status=status_filter if status_filter != "All" else None,
                search=search_kw.strip() if search_kw else None,
                limit=limit_val,
            )
            logs = data.get("logs", [])
        except Exception as exc:
            st.error(f"Error loading execution logs: {exc}")
            return

    # KPI Summary Cards
    tot_logs = len(logs)
    success_count = sum(1 for l in logs if l.get("status") == "success")
    error_count = sum(1 for l in logs if l.get("status") != "success")
    avg_latency = (sum(l.get("latency_ms", 0) for l in logs) / tot_logs) if tot_logs > 0 else 0.0

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.metric("Total Executed Tasks", tot_logs)
    with kpi2:
        st.metric("Successful Dispatches", success_count)
    with kpi3:
        st.metric("Failed / Errors", error_count)
    with kpi4:
        st.metric("Avg Latency", f"{avg_latency:.1f} ms")

    st.divider()

    if not logs:
        st.info("No execution logs match the selected filter criteria. Run an instruction in the Chat interface to trigger API executions!")
        return

    st.markdown(f"**Displaying {len(logs)} log records:**")

    for entry in logs:
        log_id = entry.get("id")
        tool_name = entry.get("tool_name", "unknown_tool")
        status_val = entry.get("status", "unknown").upper()
        latency = entry.get("latency_ms", 0.0)
        req_id = entry.get("request_id", "")
        u_id = entry.get("user_id", "")
        ts = str(entry.get("created_at", ""))[:19]
        error_msg = entry.get("error_message")

        badge_bg = "#198754" if status_val == "SUCCESS" else "#dc3545"

        with st.container(border=True):
            col_l1, col_l2, col_l3 = st.columns([2.5, 1.5, 1])

            with col_l1:
                st.markdown(f"### ⚙️ `{tool_name}`")
                st.caption(f"Request ID: `{req_id}` | Time: `{ts}`")
                if is_admin and u_id:
                    st.caption(f"User ID: `{u_id}`")

            with col_l2:
                st.write(f"⏱️ **Latency:** `{latency:.1f} ms`")
                if error_msg:
                    st.error(f"⚠️ {error_msg}")

            with col_l3:
                st.markdown(
                    f"<div style='text-align: right;'><span style='background-color: {badge_bg}; color: white; padding: 4px 10px; border-radius: 4px; font-weight: bold; font-size: 12px;'>{status_val}</span></div>",
                    unsafe_allow_html=True,
                )

            # Expandable payload inspector
            with st.expander("🔍 Inspect Input Arguments & Response Payload"):
                col_sub1, col_sub2 = st.columns(2)
                with col_sub1:
                    st.markdown("**Input Arguments Dispatched:**")
                    st.json(entry.get("input_args") or {})
                with col_sub2:
                    st.markdown("**API Response Payload / Result:**")
                    st.json(entry.get("response_payload") or {})

