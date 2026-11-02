"""
Frontend HTTP client for the FastAPI backend.

The Streamlit UI communicates strictly through this client, never touching
PostgreSQL, Qdrant, or external APIs directly.
"""

from __future__ import annotations

import os
from typing import Any, Optional
import httpx

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")


class BackendClient:
    """Interacts with the FastAPI backend using authenticated bearer sessions."""

    def __init__(self, base_url: str = BACKEND_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ── Auth ─────────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate user against /auth/login."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{self.base_url}/auth/login",
                json={"email": email, "password": password},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    def get_me(self, token: str) -> dict[str, Any]:
        """Get current profile from /auth/me."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/auth/me", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    # ── Chat ─────────────────────────────────────────────────────────────────

    def send_chat(self, token: str, query: str, conversation_id: Optional[str] = None) -> dict[str, Any]:
        """Post chat request to /chat."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/chat",
                json={"query": query, "conversation_id": conversation_id},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def submit_approval(self, token: str, run_id: str, action: str, reason: Optional[str] = None) -> dict[str, Any]:
        """Approve or reject a pending run."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{self.base_url}/runs/{run_id}/approval",
                json={"action": action, "reason": reason},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    # ── Tools ────────────────────────────────────────────────────────────────

    def get_tools(self, token: str, domain: Optional[str] = None) -> dict[str, Any]:
        """Fetch registered tools list."""
        params = {"domain": domain} if domain else {}
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/tools", params=params, headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def create_admin_tool(self, token: str, tool_data: dict[str, Any]) -> dict[str, Any]:
        """Admin creates a new technical API tool."""
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{self.base_url}/admin/tools",
                json=tool_data,
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def patch_tool_status(self, token: str, tool_id: str, status: str) -> dict[str, Any]:
        """Admin changes tool status."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.patch(
                f"{self.base_url}/admin/tools/{tool_id}/status",
                json={"status": status},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def test_admin_tool(self, token: str, tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Admin tests tool configuration."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/admin/tools/{tool_id}/test",
                json={"test_mode": "mock", "input_arguments": args},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    # ── Documents & Sources ──────────────────────────────────────────────────

    def get_sources(self, token: str) -> list[dict[str, Any]]:
        """List indexed documents."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/sources", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def add_source_url(self, token: str, name: str, url: str, domain: str, description: str) -> dict[str, Any]:
        """Admin ingests a URL source."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/admin/sources/url",
                json={"name": name, "url": url, "domain": domain, "description": description},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def add_source_text(self, token: str, name: str, content: str, domain: str, description: str) -> dict[str, Any]:
        """Admin uploads text or documentation content."""
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{self.base_url}/admin/sources/upload",
                json={"name": name, "content": content, "domain": domain, "description": description},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def delete_source(self, token: str, source_id: str) -> dict[str, Any]:
        """Admin deletes a source."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.delete(f"{self.base_url}/admin/sources/{source_id}", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    # ── Usage & Activity ─────────────────────────────────────────────────────

    def get_my_usage(self, token: str) -> dict[str, Any]:
        """Get personal usage summary for normal user."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/usage/me", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def get_admin_users(self, token: str) -> list[dict[str, Any]]:
        """Admin gets all users summary table."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/admin/users", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def get_admin_user_executions(self, token: str, user_id: str) -> list[dict[str, Any]]:
        """Admin inspects user executions."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/admin/users/{user_id}/executions", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def get_admin_user_tokens(self, token: str, user_id: str) -> list[dict[str, Any]]:
        """Admin inspects user token breakdown."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/admin/users/{user_id}/tokens", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def get_my_activity(self, token: str, limit: int = 100) -> list[dict[str, Any]]:
        """Each of the caller's own tasks with its token cost and tool log rows."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{self.base_url}/usage/me/activity",
                params={"limit": limit},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json().get("activity", [])

    def get_admin_user_activity(self, token: str, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Admin: a target user's tasks, each with its tokens and its tool log rows."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{self.base_url}/admin/users/{user_id}/activity",
                params={"limit": limit},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json().get("activity", [])

    def get_admin_summary(self, token: str) -> dict[str, Any]:
        """Admin overall platform metrics."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/admin/usage/summary", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    # ── Task Execution Logs (Both Users & Admin) ─────────────────────────────

    def get_execution_logs(
        self,
        token: str,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Fetch executed task logs (filtered by user if normal user, or all if admin)."""
        params: dict[str, Any] = {"limit": limit}
        if status and status != "All":
            params["status"] = status
        if search:
            params["search"] = search

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{self.base_url}/logs", params=params, headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    # ── Model Context Protocol (MCP) Integration ─────────────────────────────

    def get_mcp_servers(self, token: str) -> dict[str, Any]:
        """Fetch configured MCP servers."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/mcp/servers", headers=self._headers(token))
            resp.raise_for_status()
            return resp.json()

    def discover_mcp_tools(self, server_name: str, token: str) -> dict[str, Any]:
        """Discover tools exposed by an MCP server."""
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{self.base_url}/mcp/servers/discover",
                json={"server_name": server_name},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def register_mcp_tools(self, server_name: str, tool_names: Optional[list[str]], token: str) -> dict[str, Any]:
        """Register discovered MCP tools into the platform catalog (Admin only)."""
        with httpx.Client(timeout=20.0) as client:
            payload: dict[str, Any] = {"server_name": server_name}
            if tool_names:
                payload["tool_names"] = tool_names
            resp = client.post(
                f"{self.base_url}/mcp/servers/register-tools",
                json=payload,
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    def execute_mcp_tool_direct(self, server_name: str, tool_name: str, arguments: dict[str, Any], token: str) -> dict[str, Any]:
        """Directly test an MCP tool."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/mcp/tools/execute",
                json={"server_name": server_name, "tool_name": tool_name, "arguments": arguments},
                headers=self._headers(token),
            )
            resp.raise_for_status()
            return resp.json()


