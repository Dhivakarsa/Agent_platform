"""
Tool Catalog component (Screen 2 - Tab A).
"""

from __future__ import annotations

import streamlit as st
from frontend.components.tool_creation import render_tool_creation
from frontend.services.backend_client import BackendClient


def render_tool_catalog(client: BackendClient) -> None:
    """Render tool catalog browsing, search, status management, and admin tool creation."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})
    is_admin = user.get("role") == "admin"

    st.subheader("🔧 Registered API Tools Catalog")
    st.caption("Browse all declarative API tools indexed for dynamic agent retrieval.")

    # Admin: Toggle between Browse Tools and Create New Tool
    if is_admin:
        catalog_mode = st.radio(
            "Mode",
            ["📋 Browse Registered Tools", "➕ Create New Tool (Admin)"],
            horizontal=True,
            label_visibility="collapsed",
        )
        if catalog_mode == "➕ Create New Tool (Admin)":
            render_tool_creation(client)
            return

    # Fetch tools from backend
    with st.spinner("Loading tool catalog..."):
        try:
            tools_data = client.get_tools(token)
            tools = tools_data.get("tools", [])
        except Exception as exc:
            st.error(f"Error fetching tool catalog: {exc}")
            return

    # Filters
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        search_query = st.text_input("🔍 Search by tool name or description", placeholder="e.g. invoice, refund, dispute...")
    with col_f2:
        categories = ["All"] + sorted(list({t.get("category", "general") for t in tools}))
        selected_cat = st.selectbox("Filter Category", categories)
    with col_f3:
        methods = ["All", "GET", "POST", "PUT", "PATCH", "DELETE"]
        selected_method = st.selectbox("Filter HTTP Method", methods)

    # Apply filters
    filtered_tools = tools
    if search_query:
        q = search_query.lower()
        filtered_tools = [
            t for t in filtered_tools
            if q in t.get("name", "").lower() or q in t.get("description", "").lower() or q in t.get("display_name", "").lower()
        ]
    if selected_cat != "All":
        filtered_tools = [t for t in filtered_tools if t.get("category") == selected_cat]
    if selected_method != "All":
        filtered_tools = [t for t in filtered_tools if t.get("method") == selected_method]

    st.markdown(f"**Showing {len(filtered_tools)} of {len(tools)} registered tools:**")

    # Render tool cards
    for tool in filtered_tools:
        with st.container(border=True):
            col_t1, col_t2, col_t3 = st.columns([2.5, 1.5, 1])

            with col_t1:
                st.markdown(f"### `{tool.get('name')}`")
                st.write(f"**{tool.get('display_name', '')}**")
                st.write(tool.get("description", ""))

            with col_t2:
                st.markdown(f"**Method & Path:** `{tool.get('method')} {tool.get('path')}`")
                st.markdown(f"**Category:** `{tool.get('category')}`")
                st.markdown(f"**Service:** `{tool.get('service', 'PayPal')}` | **v{tool.get('version', '1.0.0')}**")

                flags = []
                if tool.get("is_mutating"):
                    flags.append("⚡ Mutating")
                if tool.get("approval_required"):
                    flags.append("⚠️ Requires Approval")
                if tool.get("source_type") == "mcp" or "mcp_" in tool.get("name", ""):
                    flags.append("🔌 MCP")
                else:
                    flags.append("🌐 REST")
                if flags:
                    st.caption(" | ".join(flags))

            with col_t3:
                status_val = tool.get("status", "enabled").upper()
                badge_color = "#198754" if status_val == "ENABLED" else "#6c757d"
                st.markdown(
                    f"<span style='background-color: {badge_color}; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;'>{status_val}</span>",
                    unsafe_allow_html=True,
                )

                # Admin Controls: Status Toggling & Test
                if is_admin:
                    tool_id = tool.get("tool_id", tool.get("id"))
                    current_status = tool.get("status", "enabled")
                    new_status = "disabled" if current_status == "enabled" else "enabled"
                    btn_label = "Disable" if current_status == "enabled" else "Enable"

                    if st.button(btn_label, key=f"status_{tool.get('name')}"):
                        client.patch_tool_status(token, tool_id, new_status)
                        st.success(f"Tool {tool.get('name')} marked as {new_status}")
                        st.rerun()

            # Expand for dependencies & test
            with st.expander("🔬 Technical Details & Dependencies"):
                deps = tool.get("dependencies", [])
                if deps:
                    st.write("**Prerequisite Dependencies:**")
                    st.json(deps)
                else:
                    st.caption("No prerequisite tool dependencies required.")

                if is_admin:
                    st.markdown("**Run Mock Test:**")
                    if st.button("🧪 Execute Test Call", key=f"test_{tool.get('name')}"):
                        test_res = client.test_admin_tool(token, tool.get("tool_id", tool.get("id")), {"test": True})
                        st.write("Response Status:", test_res.get("status"))
                        st.json(test_res.get("response_payload", {}))

