"""Administrator User Management & Multi-User Activity component (Screen 3 - Admin View)."""

from __future__ import annotations

import streamlit as st

from frontend.components.activity_table import render_task_token_table
from frontend.services.backend_client import BackendClient


def render_user_management(client: BackendClient) -> None:
    """Render the administrator user directory with a per-user task and token drilldown."""
    token = st.session_state.get("token")

    st.subheader("👥 System User Management & Token Usage")
    st.caption("Expand a user to see every task they executed and what each task cost in tokens.")

    with st.spinner("Loading administrative analytics..."):
        try:
            kpi = client.get_admin_summary(token)
            users = client.get_admin_users(token)
        except Exception as exc:
            st.error(f"Error loading admin analytics: {exc}")
            return

    col_k1, col_k2, col_k3, col_k4 = st.columns(4)
    with col_k1:
        st.metric("Total Platform Users", kpi.get("total_users", len(users)))
    with col_k2:
        st.metric("Total Agent Runs", kpi.get("total_executions", 0))
    with col_k3:
        st.metric("Total Model Tokens", f"{kpi.get('total_tokens_consumed', 0):,}")
    with col_k4:
        st.metric("Active Tools in Catalog", kpi.get("active_tools_count", 0))

    st.divider()
    st.markdown("### 📋 Registered Users")

    if not users:
        st.info("No users registered yet.")
        return

    for u in users:
        user_id = u.get("user_id")
        email = u.get("email")
        role = u.get("role", "user").upper()
        tasks = u.get("total_tasks", 0)
        tokens = u.get("total_tokens", 0)

        with st.expander(
            f"👤 {email} | {role} | Tasks: {tasks} | Tokens: {tokens:,} | {u.get('status', 'Active')}"
        ):
            col_ov1, col_ov2 = st.columns(2)
            with col_ov1:
                st.write(f"• **User ID:** `{user_id}`")
                st.write(f"• **Email:** `{email}`")
                st.write(f"• **Role:** `{role}`")
            with col_ov2:
                st.write(f"• **Status:** `{u.get('status', 'Active')}`")
                st.write(f"• **Created:** `{str(u.get('created_at', ''))[:19]}`")
                st.write(f"• **Total Executions:** `{tasks}`")

            st.divider()
            st.markdown("#### Executed Tasks & Token Usage")

            try:
                activity = client.get_admin_user_activity(token, user_id)
            except Exception as exc:
                st.caption(f"Could not load activity: {exc}")
                continue

            render_task_token_table(activity, key_prefix=f"admin_{user_id}")

            tools_used = sorted({t for item in activity for t in (item.get("tools_used") or [])})
            if tools_used:
                st.markdown("**Tools this user has dispatched:**")
                st.write(", ".join(f"`{t}`" for t in tools_used))
