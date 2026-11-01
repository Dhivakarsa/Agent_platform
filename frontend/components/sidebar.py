"""
Sidebar navigation and profile component.
"""

from __future__ import annotations

import streamlit as st


def render_sidebar() -> str:
    """Render sidebar with user profile, navigation buttons, and test scenarios."""
    user = st.session_state.get("user", {})
    email = user.get("email", "unknown@domain.com")
    role = user.get("role", "user").upper()

    with st.sidebar:
        # Profile badge
        badge_bg = "#dc3545" if role == "ADMIN" else "#0d6efd"
        st.markdown(
            f"""
            <div style="background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 12px; margin-bottom: 20px;">
                <div style="font-size: 13px; color: #6c757d;">Logged in as:</div>
                <div style="font-weight: bold; font-size: 14px; word-break: break-all;">{email}</div>
                <div style="margin-top: 6px;">
                    <span style="background-color: {badge_bg}; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">
                        {role}
                    </span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.subheader("🧭 Navigation")
        screens = ["💬 Chat", "🔧 Tools & Sources", "📊 Usage & Activity", "📜 Task Logs"]
        current = st.session_state.get("current_screen", "💬 Chat")

        # Map current screen string cleanly
        default_index = 0
        if "Tools" in current:
            default_index = 1
        elif "Usage" in current:
            default_index = 2
        elif "Logs" in current:
            default_index = 3

        selected_screen = st.radio(
            "Go to",
            screens,
            index=default_index,
            label_visibility="collapsed",
        )
        st.session_state.current_screen = selected_screen

        st.divider()

        # Assessment quick scenarios
        st.subheader("🎯 Assessment Scenarios")
        st.caption("Click to test specific workflows:")

        scenarios = [
            ("PayPal Invoicing", "Send an invoice for $50 to client@example.com"),
            ("PayPal Reporting", "What was my total sales volume last month?"),
            ("PayPal Disputes", "Is there a dispute open from user_123?"),
            ("Meta-Tool 1 (RAG)", "What are the valid refund reason codes for PayPal?"),
            ("Meta-Tool 2 (Capabilities)", "What tools are available for managing invoices?"),
            ("Meta-Tool 2 (Audit Logs)", "What was the status of my last request?"),
            ("MCP 1 (Filesystem)", "List all files available in sandbox storage"),
            ("MCP 2 (Database)", "Describe the database tables and record counts"),
        ]

        for label, prompt in scenarios:
            if st.button(label, use_container_width=True):
                st.session_state.active_chat_prompt = prompt
                st.session_state.current_screen = "💬 Chat"
                st.rerun()

        st.divider()

        if st.button("🚪 Log Out", use_container_width=True):
            st.session_state.token = None
            st.session_state.user = None
            st.session_state.messages = []
            st.rerun()

    return selected_screen

