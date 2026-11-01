"""
MCP Server Management & Tool Discovery component.
"""

from __future__ import annotations

import json
import streamlit as st
from frontend.services.backend_client import BackendClient


def render_mcp_catalog(client: BackendClient) -> None:
    """Render the Model Context Protocol (MCP) server management, discovery, and testing workbench."""
    token = st.session_state.get("token")
    user = st.session_state.get("user", {})
    is_admin = user.get("role") == "admin"

    st.subheader("🔌 Model Context Protocol (MCP) Integration")
    st.caption(
        "Connect open-source and local MCP servers into the platform. "
        "Discovered tools are normalized, registered in the SQL catalog, and indexed in Qdrant for agent discovery."
    )

    # 1. Fetch configured MCP servers
    try:
        servers_data = client.get_mcp_servers(token)
        servers = servers_data.get("servers", [])
    except Exception as exc:
        st.error(f"Failed to load MCP servers: {exc}")
        return

    # Server Status Cards
    st.markdown("### 📡 Registered MCP Servers")
    cols = st.columns(len(servers) if servers else 1)
    for idx, s in enumerate(servers):
        with cols[idx]:
            with st.container(border=True):
                st.markdown(f"**{s.get('display_name')}**")
                st.caption(f"`{s.get('name')}` | Type: `{s.get('server_type')}`")
                st.write(s.get("description", ""))
                st.write(f"⚙️ Discovered Tools: **{s.get('discovered_tools_count', 0)}**")

    st.divider()

    # 2. Server Explorer & Tool Discovery
    st.markdown("### 🔍 Discover & Sync Tools from MCP Server")
    server_names = [s.get("name") for s in servers]
    selected_server = st.selectbox("Select MCP Server to Inspect", server_names)

    col_btn1, col_btn2 = st.columns([1, 2])
    with col_btn1:
        discover_clicked = st.button("🔎 Discover Exposed Tools", use_container_width=True)

    if discover_clicked or st.session_state.get("last_mcp_server") == selected_server:
        st.session_state["last_mcp_server"] = selected_server
        with st.spinner(f"Querying tools/list on '{selected_server}'..."):
            try:
                disc_res = client.discover_mcp_tools(selected_server, token)
                st.session_state[f"mcp_tools_{selected_server}"] = disc_res.get("tools", [])
            except Exception as exc:
                st.error(f"Error querying tools on {selected_server}: {exc}")

    discovered_tools = st.session_state.get(f"mcp_tools_{selected_server}", [])

    if discovered_tools:
        st.success(f"Discovered **{len(discovered_tools)}** tools from `{selected_server}`.")

        # Admin sync action
        if is_admin:
            with col_btn2:
                if st.button(f"📥 Sync & Index All Tools into Catalog ({selected_server})", type="primary", use_container_width=True):
                    with st.spinner("Registering in SQL and generating Qdrant embeddings..."):
                        try:
                            sync_res = client.register_mcp_tools(selected_server, None, token)
                            st.success(f"✅ {sync_res.get('message')}")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Failed to register MCP tools: {exc}")

        # Display discovered tools
        for tool in discovered_tools:
            name = tool.get("name")
            remote_name = tool.get("remote_tool_name")
            desc = tool.get("description")
            is_reg = tool.get("is_already_registered", False)
            params = tool.get("parameters", [])

            with st.expander(f"🛠️ `{name}` ({remote_name}) {'✅ Registered' if is_reg else '⚡ Unregistered'}"):
                st.markdown(f"**Description:** {desc}")
                st.markdown(f"**Category:** `{tool.get('category')}` | **Mutating:** `{tool.get('is_mutating')}` | **Requires Approval:** `{tool.get('approval_required')}`")
                st.markdown("**Parameters:**")
                if params:
                    for p in params:
                        req_str = "*(required)*" if p.get("required") else "*(optional)*"
                        st.markdown(f"- `{p.get('name')}` ({p.get('type')}): {p.get('description')} {req_str}")
                else:
                    st.caption("No parameters required.")

                st.markdown("**JSON Schema:**")
                st.json(tool.get("input_schema", {}))

    st.divider()

    # 3. Interactive Direct Test Workbench
    st.markdown("### 🧪 Direct MCP Tool Test Workbench")
    st.caption("Quickly invoke an MCP tool with test arguments to inspect raw output.")

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        test_server = st.selectbox("Test Server", server_names, key="test_mcp_server")
    with col_t2:
        test_tools_list = [t.get("remote_tool_name") for t in st.session_state.get(f"mcp_tools_{test_server}", [])]
        if not test_tools_list:
            test_tools_list = ["list_directory", "fetch_url", "describe_database", "git_status"]
        test_tool = st.selectbox("Tool Name", test_tools_list, key="test_mcp_tool")

    # Sample default arguments
    default_args = "{}"
    if test_tool == "fetch_url":
        default_args = json.dumps({"url": "https://httpbin.org/get"}, indent=2)
    elif test_tool == "read_file":
        default_args = json.dumps({"filename": "welcome.txt"}, indent=2)
    elif test_tool == "write_file":
        default_args = json.dumps({"filename": "demo_report.txt", "content": "Exported financial data at 2026-11-01."}, indent=2)
    elif test_tool == "query_table_readonly":
        default_args = json.dumps({"table_name": "tool_definitions", "limit": 3}, indent=2)

    test_args_str = st.text_area("Arguments (JSON)", value=default_args, height=120)

    if st.button("🚀 Execute MCP Tool", type="secondary"):
        try:
            parsed_args = json.loads(test_args_str) if test_args_str.strip() else {}
        except Exception as exc:
            st.error(f"Invalid JSON in arguments: {exc}")
            return

        with st.spinner("Invoking MCP tool..."):
            try:
                res = client.execute_mcp_tool_direct(test_server, test_tool, parsed_args, token)
                st.success("Execution Completed!")
                st.json(res)
            except Exception as exc:
                st.error(f"Tool invocation failed: {exc}")

