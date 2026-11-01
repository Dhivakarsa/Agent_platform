"""
Login screen component for the Datazoic Agentic Platform.
"""

from __future__ import annotations

import streamlit as st
from frontend.services.backend_client import BackendClient


def render_login(client: BackendClient) -> None:
    """Render authenticated login screen with demo credentials assistance."""
    st.markdown(
        """
        <div style="text-align: center; margin-top: 50px; margin-bottom: 30px;">
            <h2>⚡ Datazoic Agentic Platform</h2>
            <p style="color: #6c757d;">Local Agentic API Orchestration Platform (50 to 1,000+ Tools)</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1.4, 1])

    with col2:
        with st.container(border=True):
            st.subheader("🔐 Sign In")
            st.caption("Enter your credentials or select a local demo account below:")

            # Quick fill demo buttons
            st.markdown("**Quick Demo Credentials:**")
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("👑 Admin Demo", use_container_width=True):
                    st.session_state.demo_email = "admin@examplemail.com"
                    st.session_state.demo_pass = "admin@123"
            with btn_col2:
                if st.button("👤 User Demo", use_container_width=True):
                    st.session_state.demo_email = "user@examplemail.com"
                    st.session_state.demo_pass = "user@123"

            email_val = st.session_state.get("demo_email", "")
            pass_val = st.session_state.get("demo_pass", "")

            with st.form("login_form"):
                email = st.text_input("Email", value=email_val, placeholder="admin@examplemail.com")
                password = st.text_input("Password", value=pass_val, type="password", placeholder="••••••••")
                submitted = st.form_submit_button("Sign In", use_container_width=True, type="primary")

                if submitted:
                    if not email or not password:
                        st.error("Please provide both email and password.")
                    else:
                        with st.spinner("Authenticating..."):
                            try:
                                auth_res = client.login(email.strip(), password)
                                st.session_state.token = auth_res["access_token"]
                                st.session_state.user = auth_res["user"]
                                st.session_state.current_screen = "Chat"
                                st.success("Authentication successful!")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Login failed: {exc}")

            st.caption("🔒 Passwords are verified against local salted hashes stored in PostgreSQL.")

