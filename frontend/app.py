"""
Main Streamlit application entrypoint for the Datazoic Agentic Platform.

Coordinates:
- Authentication & Session State Management
- Sidebar Navigation & Profile Display
- Screen 1: Chat Interface
- Screen 2: Tools & Sources (Tabs: Tools & Documents)
- Screen 3: Usage & Activity (Admin Multi-User View vs Normal User Personal View)
"""

from __future__ import annotations

import sys
from pathlib import Path
import streamlit as st

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from frontend.components.chat import render_chat
from frontend.components.login import render_login
from frontend.components.mcp_catalog import render_mcp_catalog
from frontend.components.sidebar import render_sidebar
from frontend.components.source_catalog import render_source_catalog
from frontend.components.task_logs import render_task_logs
from frontend.components.tool_catalog import render_tool_catalog
from frontend.components.usage_dashboard import render_user_usage
from frontend.components.user_management import render_user_management
from frontend.services.backend_client import BackendClient

# Page configuration
st.set_page_config(
    page_title="Datazoic Agentic Platform",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling
st.markdown(
    """
    <style>
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #e9ecef;
        margin-bottom: 10px;
    }
    .badge {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: bold;
        background-color: #0d6efd;
        color: white;
        margin-right: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def main() -> None:
    client = BackendClient()

    # Authentication guard
    token = st.session_state.get("token")
    if not token:
        render_login(client)
        return

    # Render sidebar and get current screen selection
    selected_screen = render_sidebar()
    user = st.session_state.get("user", {})
    is_admin = user.get("role") == "admin"

    # Route screens
    if "Chat" in selected_screen:
        render_chat(client)

    elif "Tools" in selected_screen:
        st.header("🔧 Tools & Sources Management")
        tab_tools, tab_sources, tab_mcp = st.tabs([
            "⚙️ Registered Tools",
            "📚 Documents & Knowledge Base",
            "🔌 MCP Servers & Integrations",
        ])
        with tab_tools:
            render_tool_catalog(client)
        with tab_sources:
            render_source_catalog(client)
        with tab_mcp:
            render_mcp_catalog(client)

    elif "Usage" in selected_screen:
        if is_admin:
            # Administrators see multi-user drilldown dashboard
            render_user_management(client)
        else:
            # Normal users see strictly their personal usage
            render_user_usage(client)

    elif "Logs" in selected_screen:
        render_task_logs(client)


if __name__ == "__main__":
    main()
