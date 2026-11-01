"""
Model Context Protocol (MCP) Client & Integration Service.

Provides:
- Native zero-dependency implementations of high-priority MCP tools (Fetch, Filesystem, SQLite, Git)
- Stdio JSON-RPC 2.0 protocol bridge for external open-source MCP servers (npx, uvx, python)
- Dynamic discovery, schema normalization, and synchronization into SQL + Qdrant
- Secure sandboxed execution router with parameter validation and path containment
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import MCPServerDefinition, ToolDefinition
from app.db.session import async_session_factory
from app.schemas.mcp import (
    MCPDiscoveredTool,
    MCPDiscoverResponse,
    MCPServerConfig,
)
from app.schemas.tool import ParameterSchema, RegisteredTool, ToolSourceType

logger = logging.getLogger(__name__)


# Standard Sandbox Storage directory for Filesystem MCP operations
SANDBOX_STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "sandbox_storage"


class MCPClientService:
    """Manages MCP server discovery, connections, and tool dispatch."""

    def __init__(self) -> None:
        self.settings = get_settings()
        SANDBOX_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        # Seed a sample readme in sandbox if empty
        welcome_file = SANDBOX_STORAGE_DIR / "welcome.txt"
        if not welcome_file.exists():
            welcome_file.write_text(
                "Welcome to the Datazoic Sandbox Storage.\n"
                "This directory is managed via the Filesystem MCP Server.\n"
                "Files created or processed by agent tools are securely contained here.\n",
                encoding="utf-8",
            )

    # ── 1. Built-in Server Definitions ───────────────────────────────────────

    def get_default_servers(self) -> List[MCPServerConfig]:
        """Return canonical list of recommended and built-in MCP servers."""
        return [
            MCPServerConfig(
                name="fetch",
                display_name="Fetch MCP Server",
                description="Fetches public web pages, API documentation, and extracts clean markdown for RAG.",
                server_type="builtin",
                command="builtin",
                is_active=True,
                discovered_tools_count=2,
            ),
            MCPServerConfig(
                name="filesystem",
                display_name="Filesystem MCP Server",
                description="Securely inspects, reads, and writes files inside the approved sandbox_storage directory.",
                server_type="builtin",
                command="builtin",
                is_active=True,
                discovered_tools_count=4,
            ),
            MCPServerConfig(
                name="sqlite_diagnostics",
                display_name="SQLite Diagnostics MCP Server",
                description="Read-only schema inspection and diagnostic queries against application tables.",
                server_type="builtin",
                command="builtin",
                is_active=True,
                discovered_tools_count=2,
            ),
            MCPServerConfig(
                name="git_tools",
                display_name="Git MCP Server",
                description="Inspects local repository status, commit logs, and configuration history.",
                server_type="builtin",
                command="builtin",
                is_active=True,
                discovered_tools_count=2,
            ),
        ]

    async def list_servers(self) -> List[MCPServerConfig]:
        """List all configured MCP servers from database with defaults fallback."""
        async with async_session_factory() as s:
            stmt = select(MCPServerDefinition).order_by(MCPServerDefinition.name)
            res = await s.execute(stmt)
            rows = res.scalars().all()

            if not rows:
                # Seed defaults
                defaults = self.get_default_servers()
                for d in defaults:
                    row = MCPServerDefinition(
                        name=d.name,
                        display_name=d.display_name,
                        description=d.description,
                        server_type=d.server_type,
                        command=d.command,
                        args=d.args,
                        env_vars=d.env_vars,
                        is_active=d.is_active,
                        discovered_tools_count=d.discovered_tools_count,
                    )
                    s.add(row)
                await s.commit()
                return defaults

            configs = []
            for r in rows:
                configs.append(
                    MCPServerConfig(
                        name=r.name,
                        display_name=r.display_name,
                        description=r.description,
                        server_type=r.server_type,
                        command=r.command,
                        args=r.args or [],
                        env_vars=r.env_vars or {},
                        is_active=r.is_active,
                        discovered_tools_count=r.discovered_tools_count,
                    )
                )
            return configs

    # ── 2. Tool Discovery & Introspection ────────────────────────────────────

    async def discover_server_tools(self, server_name: str) -> MCPDiscoverResponse:
        """Query an MCP server for its exposed tool specifications."""
        # Check if builtin
        if server_name == "fetch":
            tools = self._get_fetch_tools()
        elif server_name == "filesystem":
            tools = self._get_filesystem_tools()
        elif server_name == "sqlite_diagnostics":
            tools = self._get_sqlite_tools()
        elif server_name == "git_tools":
            tools = self._get_git_tools()
        else:
            # Query external stdio server if configured
            tools = await self._discover_external_stdio(server_name)

        # Check existing registration status in DB
        async with async_session_factory() as s:
            stmt = select(ToolDefinition.name)
            res = await s.execute(stmt)
            registered_names = set(res.scalars().all())

        for t in tools:
            if t.name in registered_names:
                t.is_already_registered = True

        return MCPDiscoverResponse(
            server_name=server_name,
            server_type="builtin" if server_name in ["fetch", "filesystem", "sqlite_diagnostics", "git_tools"] else "stdio",
            status="connected",
            tools_count=len(tools),
            tools=tools,
        )

    # ── 3. Tool Registration into System Catalog & Qdrant ────────────────────

    async def register_mcp_tools(self, server_name: str, selected_tool_names: Optional[List[str]] = None) -> List[str]:
        """Import discovered MCP tools into PostgreSQL/SQLite and index them in Qdrant."""
        from app.services.tool_registry import get_tool_registry

        discovery = await self.discover_server_tools(server_name)
        registry = get_tool_registry()

        to_register = []
        for tool_meta in discovery.tools:
            if selected_tool_names and tool_meta.name not in selected_tool_names:
                continue

            param_schemas = []
            for p in tool_meta.parameters:
                param_schemas.append(
                    ParameterSchema(
                        name=p.get("name", ""),
                        type=p.get("type", "string"),
                        description=p.get("description", ""),
                        required=p.get("required", False),
                        default=p.get("default", None),
                        location="body",
                    )
                )

            registered = RegisteredTool(
                name=tool_meta.name,
                display_name=tool_meta.display_name or tool_meta.name,
                description=tool_meta.description,
                domain=tool_meta.category,
                category=tool_meta.category,
                service=f"MCP ({server_name})",
                source="mcp",
                source_type=ToolSourceType.MCP,
                mcp_server_name=server_name,
                remote_tool_name=tool_meta.remote_tool_name,
                is_mutating=tool_meta.is_mutating,
                approval_required=tool_meta.approval_required,
                parameters=param_schemas,
                request_body_schema=tool_meta.input_schema,
                status="enabled",
            )
            to_register.append(registered)

        if to_register:
            await registry.register_batch(to_register)

        # Update server discovered tools count in DB
        async with async_session_factory() as s:
            stmt = select(MCPServerDefinition).where(MCPServerDefinition.name == server_name)
            server_row = (await s.execute(stmt)).scalar_one_or_none()
            if server_row:
                server_row.discovered_tools_count = len(discovery.tools)
                await s.commit()

        return [t.name for t in to_register]

    # ── 4. Unified Tool Execution Dispatcher ─────────────────────────────────

    async def execute_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch tool execution to the appropriate native handler or stdio process."""
        logger.info("MCP Execution: server=%s, tool=%s, args=%s", server_name, tool_name, arguments)

        # Strip prefix if full canonical name was supplied
        remote_name = tool_name
        if tool_name.startswith(f"mcp_{server_name}_"):
            remote_name = tool_name[len(f"mcp_{server_name}_") :]

        if server_name == "fetch":
            return await self._execute_fetch(remote_name, arguments)
        elif server_name == "filesystem":
            return await self._execute_filesystem(remote_name, arguments)
        elif server_name == "sqlite_diagnostics":
            return await self._execute_sqlite(remote_name, arguments)
        elif server_name == "git_tools":
            return await self._execute_git(remote_name, arguments)
        else:
            return await self._execute_external_stdio(server_name, remote_name, arguments)

    # ── 5. Native Built-in Handlers ──────────────────────────────────────────

    # A. Fetch Server
    def _get_fetch_tools(self) -> List[MCPDiscoveredTool]:
        return [
            MCPDiscoveredTool(
                name="mcp_fetch_url",
                remote_tool_name="fetch_url",
                server_name="fetch",
                display_name="Fetch Web Page & Documentation",
                description="Retrieves a web page or public API documentation URL and converts it into clean readable markdown.",
                category="research",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL of the documentation or web page to fetch"},
                        "max_length": {"type": "integer", "description": "Maximum characters to return", "default": 4000},
                    },
                    "required": ["url"],
                },
                parameters=[
                    {"name": "url", "type": "string", "description": "URL to fetch", "required": True},
                    {"name": "max_length", "type": "integer", "description": "Character limit", "required": False, "default": 4000},
                ],
            ),
            MCPDiscoveredTool(
                name="mcp_fetch_api_spec",
                remote_tool_name="fetch_api_spec",
                server_name="fetch",
                display_name="Fetch & Inspect OpenAPI / Postman Spec",
                description="Fetches an OpenAPI or Postman JSON specification from a URL or local path and validates its endpoints.",
                category="research",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "spec_source": {"type": "string", "description": "URL or local file path containing the API specification"}
                    },
                    "required": ["spec_source"],
                },
                parameters=[
                    {"name": "spec_source", "type": "string", "description": "URL or file path", "required": True}
                ],
            ),
        ]

    async def _execute_fetch(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "fetch_url":
            url = args.get("url", "").strip()
            max_len = int(args.get("max_length", 4000))
            if not url:
                return {"error": "Missing required parameter 'url'"}

            # SSRF Protection: Block internal localhost / loopback addresses
            if any(h in url.lower() for h in ["127.0.0.1", "localhost", "0.0.0.0", "169.254."]):
                return {"error": "Access to local/loopback network addresses is restricted for security."}

            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url, headers={"User-Agent": "Datazoic-Agentic-System/2.0"})
                    content_type = resp.headers.get("content-type", "")
                    text = resp.text

                    # Strip HTML tags
                    clean_text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
                    clean_text = re.sub(r"<style.*?</style>", "", clean_text, flags=re.DOTALL | re.IGNORECASE)
                    clean_text = re.sub(r"<[^>]+>", " ", clean_text)
                    clean_text = re.sub(r"\s+", " ", clean_text).strip()

                    truncated = clean_text[:max_len]
                    return {
                        "url": url,
                        "status_code": resp.status_code,
                        "content_length": len(clean_text),
                        "extracted_text": truncated,
                        "truncated": len(clean_text) > max_len,
                    }
            except Exception as exc:
                return {"error": f"Failed to fetch URL {url}: {exc}"}

        elif tool_name == "fetch_api_spec":
            source = args.get("spec_source", "").strip()
            try:
                if source.startswith("http://") or source.startswith("https://"):
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.get(source)
                        data = resp.json()
                else:
                    path = Path(source)
                    if not path.exists():
                        return {"error": f"Local file not found: {source}"}
                    data = json.loads(path.read_text(encoding="utf-8"))

                endpoints = []
                if "item" in data:  # Postman
                    for it in data["item"][:15]:
                        name = it.get("name", "")
                        req = it.get("request", {})
                        method = req.get("method", "GET")
                        endpoints.append(f"{method} {name}")
                elif "paths" in data:  # OpenAPI
                    for p, ops in list(data["paths"].items())[:15]:
                        for m in ops.keys():
                            endpoints.append(f"{m.upper()} {p}")

                return {
                    "source": source,
                    "spec_type": "postman" if "item" in data else "openapi",
                    "title": data.get("info", {}).get("name") or data.get("info", {}).get("title", "API Spec"),
                    "sample_endpoints": endpoints,
                    "total_endpoints_found": len(endpoints),
                }
            except Exception as exc:
                return {"error": f"Failed to parse API specification: {exc}"}

        return {"error": f"Unknown tool '{tool_name}' for Fetch MCP server"}

    # B. Filesystem Server
    def _get_filesystem_tools(self) -> List[MCPDiscoveredTool]:
        return [
            MCPDiscoveredTool(
                name="mcp_list_directory",
                remote_tool_name="list_directory",
                server_name="filesystem",
                display_name="List Sandbox Files",
                description="Lists all files and subdirectories located inside the secure sandbox storage directory.",
                category="filesystem",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "subpath": {"type": "string", "description": "Optional relative subpath inside sandbox", "default": ""}
                    },
                },
                parameters=[
                    {"name": "subpath", "type": "string", "description": "Relative directory", "required": False, "default": ""}
                ],
            ),
            MCPDiscoveredTool(
                name="mcp_read_file",
                remote_tool_name="read_file",
                server_name="filesystem",
                display_name="Read File Content",
                description="Reads the text content of a file stored in the sandbox directory.",
                category="filesystem",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Name of the file to read"}
                    },
                    "required": ["filename"],
                },
                parameters=[
                    {"name": "filename", "type": "string", "description": "Filename", "required": True}
                ],
            ),
            MCPDiscoveredTool(
                name="mcp_write_file",
                remote_tool_name="write_file",
                server_name="filesystem",
                display_name="Write / Export File",
                description="Writes text or structured CSV reports to a designated file inside sandbox storage.",
                category="filesystem",
                is_mutating=True,
                approval_required=True,
                input_schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Name of the file to write"},
                        "content": {"type": "string", "description": "Text or data content to write"},
                    },
                    "required": ["filename", "content"],
                },
                parameters=[
                    {"name": "filename", "type": "string", "description": "Target filename", "required": True},
                    {"name": "content", "type": "string", "description": "File contents", "required": True},
                ],
            ),
            MCPDiscoveredTool(
                name="mcp_search_files",
                remote_tool_name="search_files",
                server_name="filesystem",
                display_name="Search Sandbox Files",
                description="Searches for files matching a keyword pattern inside the sandbox directory.",
                category="filesystem",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keyword or filename pattern"}
                    },
                    "required": ["query"],
                },
                parameters=[
                    {"name": "query", "type": "string", "description": "Search keyword", "required": True}
                ],
            ),
        ]

    def _resolve_safe_path(self, user_path: str) -> Optional[Path]:
        """Ensure path is contained strictly within SANDBOX_STORAGE_DIR to prevent directory traversal."""
        clean = Path(user_path).name if "/" in user_path or "\\" in user_path else user_path
        resolved = (SANDBOX_STORAGE_DIR / clean).resolve()
        if not str(resolved).startswith(str(SANDBOX_STORAGE_DIR.resolve())):
            return None
        return resolved

    async def _execute_filesystem(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "list_directory":
            files = []
            for item in SANDBOX_STORAGE_DIR.iterdir():
                stat = item.stat()
                files.append(
                    {
                        "name": item.name,
                        "is_dir": item.is_dir(),
                        "size_bytes": stat.st_size if item.is_file() else 0,
                        "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(stat.st_mtime)),
                    }
                )
            return {"directory": str(SANDBOX_STORAGE_DIR), "files_count": len(files), "files": files}

        elif tool_name == "read_file":
            filename = args.get("filename", "").strip()
            target = self._resolve_safe_path(filename)
            if not target or not target.exists() or not target.is_file():
                return {"error": f"File '{filename}' not found in sandbox storage."}
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"filename": target.name, "size_bytes": len(content), "content": content[:8000]}

        elif tool_name == "write_file":
            filename = args.get("filename", "").strip()
            content = args.get("content", "")
            target = self._resolve_safe_path(filename)
            if not target:
                return {"error": "Invalid filename or security path violation."}
            target.write_text(content, encoding="utf-8")
            return {
                "status": "success",
                "filename": target.name,
                "bytes_written": len(content.encode("utf-8")),
                "message": f"Successfully wrote to sandbox file '{target.name}'.",
            }

        elif tool_name == "search_files":
            query = args.get("query", "").lower()
            matches = []
            for item in SANDBOX_STORAGE_DIR.iterdir():
                if item.is_file():
                    if query in item.name.lower():
                        matches.append({"filename": item.name, "match_type": "filename"})
                    else:
                        text = item.read_text(encoding="utf-8", errors="ignore")
                        if query in text.lower():
                            matches.append({"filename": item.name, "match_type": "content"})
            return {"query": query, "matches_count": len(matches), "matches": matches}

        return {"error": f"Unknown filesystem tool '{tool_name}'"}

    # C. SQLite Diagnostics Server
    def _get_sqlite_tools(self) -> List[MCPDiscoveredTool]:
        return [
            MCPDiscoveredTool(
                name="mcp_describe_database",
                remote_tool_name="describe_database",
                server_name="sqlite_diagnostics",
                display_name="Describe Database Schema",
                description="Lists all tables, columns, and record counts in the system database for diagnosis.",
                category="diagnostics",
                is_mutating=False,
                approval_required=False,
                input_schema={"type": "object", "properties": {}},
                parameters=[],
            ),
            MCPDiscoveredTool(
                name="mcp_query_table_readonly",
                remote_tool_name="query_table_readonly",
                server_name="sqlite_diagnostics",
                display_name="Safe Read-Only Query",
                description="Performs safe read-only inspection query on permitted diagnostic tables (execution_log, tool_definitions).",
                category="diagnostics",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "enum": ["execution_log", "token_usage", "tool_definitions", "knowledge_sources"],
                            "description": "Allowed table to inspect",
                        },
                        "limit": {"type": "integer", "description": "Max rows to return", "default": 5},
                    },
                    "required": ["table_name"],
                },
                parameters=[
                    {"name": "table_name", "type": "string", "description": "Table to query", "required": True},
                    {"name": "limit", "type": "integer", "description": "Row limit", "required": False, "default": 5},
                ],
            ),
        ]

    async def _execute_sqlite(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from sqlalchemy import text

        if tool_name == "describe_database":
            async with async_session_factory() as s:
                # Query table names from SQLite master
                query = text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                res = await s.execute(query)
                tables = [r[0] for r in res.fetchall()]

                table_stats = []
                for t in tables:
                    cnt_res = await s.execute(text(f"SELECT COUNT(*) FROM {t};"))
                    table_stats.append({"table": t, "row_count": cnt_res.scalar()})

                return {"database": "agentic_system.db", "tables_count": len(tables), "tables": table_stats}

        elif tool_name == "query_table_readonly":
            tbl = args.get("table_name", "").strip().lower()
            allowed_tables = ["execution_log", "token_usage", "tool_definitions", "knowledge_sources"]
            if tbl not in allowed_tables:
                return {"error": f"Query on table '{tbl}' is forbidden. Allowed tables: {allowed_tables}"}

            limit = min(int(args.get("limit", 5)), 20)
            async with async_session_factory() as s:
                query = text(f"SELECT * FROM {tbl} ORDER BY id DESC LIMIT :limit")
                res = await s.execute(query, {"limit": limit})
                rows = [dict(r._mapping) for r in res.fetchall()]

                # Redact sensitive data if any
                clean_rows = []
                for r in rows:
                    row_copy = dict(r)
                    if "password_hash" in row_copy:
                        row_copy["password_hash"] = "[REDACTED]"
                    clean_rows.append(row_copy)

                return {"table": tbl, "rows_returned": len(clean_rows), "data": clean_rows}

        return {"error": f"Unknown tool '{tool_name}' for SQLite Diagnostics server"}

    # D. Git Tools Server
    def _get_git_tools(self) -> List[MCPDiscoveredTool]:
        return [
            MCPDiscoveredTool(
                name="mcp_git_status",
                remote_tool_name="git_status",
                server_name="git_tools",
                display_name="Inspect Git Status",
                description="Checks git repository branch status, staged, and unstaged modifications.",
                category="version_control",
                is_mutating=False,
                approval_required=False,
                input_schema={"type": "object", "properties": {}},
                parameters=[],
            ),
            MCPDiscoveredTool(
                name="mcp_git_log",
                remote_tool_name="git_log",
                server_name="git_tools",
                display_name="Inspect Git Commit History",
                description="Returns the most recent commit hashes, authors, dates, and commit messages.",
                category="version_control",
                is_mutating=False,
                approval_required=False,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of commits to return", "default": 5}
                    },
                },
                parameters=[
                    {"name": "limit", "type": "integer", "description": "Max commits", "required": False, "default": 5}
                ],
            ),
        ]

    async def _execute_git(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "git_status":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "status", "--short",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                return {
                    "returncode": proc.returncode,
                    "status_output": stdout.decode("utf-8", errors="replace"),
                }
            except Exception as exc:
                return {"error": f"Git command failed: {exc}"}

        elif tool_name == "git_log":
            limit = min(int(args.get("limit", 5)), 20)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "log", f"-n{limit}", "--oneline",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                commits = stdout.decode("utf-8", errors="replace").strip().splitlines()
                return {"commits_count": len(commits), "commits": commits}
            except Exception as exc:
                return {"error": f"Git log command failed: {exc}"}

        return {"error": f"Unknown git tool '{tool_name}'"}

    # ── 6. External Stdio JSON-RPC Bridge ────────────────────────────────────

    async def _discover_external_stdio(self, server_name: str) -> List[MCPDiscoveredTool]:
        """Discover tools from an external stdio MCP server via JSON-RPC 2.0."""
        # Query DB for server definition
        async with async_session_factory() as s:
            stmt = select(MCPServerDefinition).where(MCPServerDefinition.name == server_name)
            server_row = (await s.execute(stmt)).scalar_one_or_none()

        if not server_row or not server_row.command:
            logger.warning("No command configured for external MCP server '%s'", server_name)
            return []

        try:
            cmd = [server_row.command] + (server_row.args or [])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # JSON-RPC Initialize
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "datazoic-agent", "version": "2.0.0"},
                },
            }
            proc.stdin.write((json.dumps(init_req) + "\n").encode())
            await proc.stdin.drain()

            # Read response
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            resp = json.loads(line.decode())

            # JSON-RPC tools/list
            tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            proc.stdin.write((json.dumps(tools_req) + "\n").encode())
            await proc.stdin.drain()

            tools_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            tools_resp = json.loads(tools_line.decode())
            proc.kill()

            raw_tools = tools_resp.get("result", {}).get("tools", [])
            discovered = []
            for t in raw_tools:
                remote_name = t.get("name")
                discovered.append(
                    MCPDiscoveredTool(
                        name=f"mcp_{server_name}_{remote_name}",
                        remote_tool_name=remote_name,
                        server_name=server_name,
                        display_name=t.get("name", "").replace("_", " ").title(),
                        description=t.get("description", ""),
                        category="mcp_tools",
                        input_schema=t.get("inputSchema", {}),
                    )
                )
            return discovered
        except Exception as exc:
            logger.error("Failed to discover external MCP server '%s': %s", server_name, exc)
            return []

    async def _execute_external_stdio(self, server_name: str, remote_tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke tool on external stdio server via JSON-RPC 2.0 tools/call."""
        async with async_session_factory() as s:
            stmt = select(MCPServerDefinition).where(MCPServerDefinition.name == server_name)
            server_row = (await s.execute(stmt)).scalar_one_or_none()

        if not server_row or not server_row.command:
            return {"error": f"Server '{server_name}' has no command configured."}

        try:
            cmd = [server_row.command] + (server_row.args or [])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Initialize
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "datazoic-agent"}},
            }
            proc.stdin.write((json.dumps(init_req) + "\n").encode())
            await proc.stdin.drain()
            await proc.stdout.readline()

            # Call Tool
            call_req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": remote_tool_name, "arguments": arguments},
            }
            proc.stdin.write((json.dumps(call_req) + "\n").encode())
            await proc.stdin.drain()

            call_line = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
            call_resp = json.loads(call_line.decode())
            proc.kill()

            result = call_resp.get("result", {})
            return {"server": server_name, "tool": remote_tool_name, "result": result}
        except Exception as exc:
            return {"error": f"External MCP tool execution error: {exc}"}


_mcp_service: Optional[MCPClientService] = None


def get_mcp_service() -> MCPClientService:
    global _mcp_service
    if _mcp_service is None:
        _mcp_service = MCPClientService()
    return _mcp_service
