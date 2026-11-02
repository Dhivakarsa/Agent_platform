"""Task Execution Logs component (Screen 4) for both normal users and administrators."""

from __future__ import annotations

import streamlit as st

from frontend.components.activity_table import render_task_log_tables
from frontend.services.backend_client import BackendClient


def _render_admin_logs(client: BackendClient, token: str) -> None:
    """Administrator view: pick a user, then see their tasks and each task's tool log."""
    st.subheader("📜 Task Execution Logs")
    st.caption("Expand a user to see the tasks they executed, each with its tool calls and logs.")

    try:
        users = client.get_admin_users(token)
    except Exception as exc:
        st.error(f"Error loading users: {exc}")
        return

    if not users:
        st.info("No users registered yet.")
        return

    for u in users:
        user_id = u.get("user_id")
        label = (
            f"👤 {u.get('email')} | {u.get('role', 'user').upper()} "
            f"| Tasks: {u.get('total_tasks', 0)}"
        )
        with st.expander(label):
            try:
                activity = client.get_admin_user_activity(token, user_id)
            except Exception as exc:
                st.caption(f"Could not load activity: {exc}")
                continue

            if not activity:
                st.caption("This user has not executed any tasks yet.")
                continue

            total_steps = sum(a.get("step_count", 0) for a in activity)
            total_failed = sum(a.get("failed_step_count", 0) for a in activity)
            k1, k2, k3 = st.columns(3)
            with k1:
                st.metric("Tasks", len(activity))
            with k2:
                st.metric("Tool Calls", total_steps)
            with k3:
                st.metric("Failed Calls", total_failed)

            render_task_log_tables(activity, key_prefix=f"log_{user_id}")


def _render_user_logs(client: BackendClient, token: str, email: str) -> None:
    """Normal user view: only their own tasks and tool calls."""
    st.subheader("📜 My Task Execution Logs")
    st.caption(f"Every tool call dispatched on your behalf ({email}).")

    try:
        activity = client.get_my_activity(token)
    except Exception as exc:
        st.error(f"Error loading logs: {exc}")
        return

    if not activity:
        st.info("No tasks executed yet. Run an instruction in the Chat interface!")
        return

    total_steps = sum(a.get("step_count", 0) for a in activity)
    total_failed = sum(a.get("failed_step_count", 0) for a in activity)
    latencies = [s.get("latency_ms", 0.0) for a in activity for s in (a.get("steps") or [])]

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Tasks", len(activity))
    with k2:
        st.metric("Tool Calls", total_steps)
    with k3:
        st.metric("Failed Calls", total_failed)
    with k4:
        avg = sum(latencies) / len(latencies) if latencies else 0.0
        st.metric("Avg Latency", f"{avg:.0f} ms")

    search = st.text_input("🔍 Filter tasks by keyword", placeholder="e.g. invoice, dispute...")
    filtered = activity
    if search:
        needle = search.lower()
        filtered = [
            a
            for a in activity
            if needle in (a.get("task") or "").lower()
            or any(needle in str(s.get("tool_name", "")).lower() for s in (a.get("steps") or []))
        ]
        if not filtered:
            st.caption("No tasks match that filter.")

    render_task_log_tables(filtered, key_prefix="log_me")


def render_task_logs(client: BackendClient) -> None:
    """Render execution logs, grouped by user for administrators."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})

    if user.get("role") == "admin":
        _render_admin_logs(client, token)
    else:
        _render_user_logs(client, token, user.get("email", ""))
