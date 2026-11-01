"""
Administrator User Management & Multi-User Activity component (Screen 3 - Admin View).
"""

from __future__ import annotations

import streamlit as st
from frontend.services.backend_client import BackendClient


def render_user_management(client: BackendClient) -> None:
    """Render administrative user directory with expandable 4-section user drilldown."""
    token = st.session_state.get("token")

    st.subheader("👥 System User Management & Activity Tracking")
    st.caption("Inspect all registered users, overall model token usage, and granular execution audit traces.")

    # Fetch platform summary
    with st.spinner("Loading administrative analytics..."):
        try:
            kpi = client.get_admin_summary(token)
            users = client.get_admin_users(token)
        except Exception as exc:
            st.error(f"Error loading admin analytics: {exc}")
            return

    # Platform KPI row
    col_k1, col_k2, col_k3, col_k4 = st.columns(4)
    with col_k1:
        st.metric("Total Platform Users", kpi.get("total_users", len(users)))
    with col_k2:
        st.metric("Total Agent Runs", kpi.get("total_executions", 0))
    with col_k3:
        st.metric("Total Gemini Tokens", f"{kpi.get('total_tokens_consumed', 0):,}")
    with col_k4:
        st.metric("Active Tools in Catalog", kpi.get("active_tools_count", 50))

    st.divider()

    st.markdown("### 📋 Registered Users Directory")
    st.caption("Click on any user to expand their 4-section drilldown (Overview, Execution History, Token Usage, and Tools Used).")

    for u in users:
        user_id = u.get("user_id")
        email = u.get("email")
        role = u.get("role", "user").upper()
        tasks = u.get("total_tasks", 0)
        tokens = u.get("total_tokens", 0)
        status_val = u.get("status", "Active")

        # User row expander
        with st.expander(f"👤 {email} | Role: {role} | Tasks: {tasks} | Tokens: {tokens:,} | Status: {status_val}"):
            # Section 1: User Overview
            st.markdown("#### 1. User Overview")
            col_ov1, col_ov2 = st.columns(2)
            with col_ov1:
                st.write(f"• **User ID:** `{user_id}`")
                st.write(f"• **Email Address:** `{email}`")
                st.write(f"• **Assigned Role:** `{role}`")
            with col_ov2:
                st.write(f"• **Account Status:** `{status_val}`")
                st.write(f"• **Account Created:** `{u.get('created_at', '')[:19]}`")
                st.write(f"• **Total Executions:** `{tasks}`")

            st.divider()

            # Load drilldown details on demand
            col_d1, col_d2 = st.columns(2)

            # Section 2: Execution History
            with col_d1:
                st.markdown("#### 2. Execution History")
                try:
                    user_execs = client.get_admin_user_executions(token, user_id)
                    if user_execs:
                        for idx, ex in enumerate(user_execs[:5]):
                            st.write(f"**[{ex.get('status').upper()}]** `{ex.get('query')}` ({ex.get('duration_ms', 0):.0f}ms)")
                            st.caption(f"Req: `{ex.get('run_id')}` | Tools: {', '.join(ex.get('tools_used', []))}")
                    else:
                        st.caption("No executions recorded for this user.")
                except Exception as exc:
                    st.caption(f"Could not load execution history: {exc}")

            # Section 3: Token Usage
            with col_d2:
                st.markdown("#### 3. Gemini Token Breakdown")
                try:
                    user_toks = client.get_admin_user_tokens(token, user_id)
                    if user_toks:
                        for tk in user_toks[:5]:
                            st.write(f"• **{tk.get('model')}**: `{tk.get('total_tokens')}` tokens (In: {tk.get('input_tokens')}, Out: {tk.get('output_tokens')})")
                            st.caption(f"Req: `{tk.get('request_id')}` | Date: {tk.get('created_at')[:19]}")
                    else:
                        st.caption("No token consumption records logged.")
                except Exception as exc:
                    st.caption(f"Could not load token usage: {exc}")

            st.divider()

            # Section 4: Tools & Sources Used
            st.markdown("#### 4. Tools & Sources Used by User")
            if user_execs:
                tools_set = set()
                for ex in user_execs:
                    for t in ex.get("tools_used", []):
                        tools_set.add(t)
                if tools_set:
                    st.write("**Dispatched APIs:**", ", ".join(f"`{t}`" for t in tools_set))
                else:
                    st.caption("User has not triggered any operational tools yet.")
            else:
                st.caption("No tool usage data available.")

