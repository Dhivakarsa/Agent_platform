"""
Pinned Meta-Tool 2: System Search Tool.

Queries the system's own capabilities (registered tool catalog) or execution audit logs.
Answers questions like "What tools are available for managing invoices?" or "What's the status of my last request?".
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional
from sqlalchemy import desc, or_, select

from app.db.models import ExecutionLog, ToolMetadata
from app.db.session import async_session_factory
from app.schemas.tool import ParameterSchema, RegisteredTool
from app.services.tool_registry import ToolRegistry, get_tool_registry

logger = logging.getLogger(__name__)


class SystemSearchTool:
    """Introspects registered system tools and execution logs."""

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self.registry = registry or get_tool_registry()

    async def query(
        self,
        scope: Literal["capabilities", "execution_logs"],
        query: str = "",
        limit: int = 5,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_role: str = "user",
    ) -> dict[str, Any]:
        """
        Execute introspection query:
        - 'capabilities': Search tool catalog metadata for available APIs.
        - 'execution_logs': Query past tool runs and statuses for audit / status checks.
        """
        logger.info("SystemSearchTool query [scope=%s, query=%r, session_id=%s]", scope, query, session_id)

        if scope == "capabilities":
            return await self._search_capabilities(query=query, limit=limit)
        elif scope == "execution_logs":
            return await self._search_logs(
                query=query,
                limit=limit,
                session_id=session_id,
                user_id=user_id,
                user_role=user_role,
            )
        else:
            return {"error": f"Invalid scope '{scope}'. Allowed values are 'capabilities' or 'execution_logs'."}

    async def _search_capabilities(self, query: str, limit: int) -> dict[str, Any]:
        """Search available tools from registry metadata."""
        matches = await self.registry.search_by_metadata(query=query, limit=limit)
        if not matches and not query:
            matches = await self.registry.list_tools()

        tools_data = [
            {
                "name": t.name,
                "domain": t.domain,
                "method": t.method,
                "path": t.path,
                "description": t.description,
                "is_mutating": t.is_mutating,
                "requires_prior": t.requires_prior,
            }
            for t in matches[:limit]
        ]

        return {
            "scope": "capabilities",
            "query": query,
            "total_registered_tools": self.registry.get_tool_count(),
            "matched_tools": tools_data,
        }

    async def _search_logs(
        self,
        query: str,
        limit: int,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_role: str = "user",
    ) -> dict[str, Any]:
        """
        Search execution logs, scoped to the calling user unless they are an admin.

        The keyword filter is applied against tool name and request id only. Callers
        routinely pass conversational phrasing ("my last request"), so when a filter
        matches nothing the most recent entries are returned instead of an empty list.
        """
        async with async_session_factory() as s:

            def base_query():
                stmt = select(ExecutionLog).order_by(desc(ExecutionLog.id))
                # Normal users may only introspect their own execution history.
                if user_role != "admin" and user_id:
                    stmt = stmt.where(ExecutionLog.user_id == user_id)
                if session_id:
                    stmt = stmt.where(ExecutionLog.session_id == session_id)
                return stmt

            logs = []
            filtered = False
            if query:
                stmt = base_query().where(
                    or_(
                        ExecutionLog.tool_name.ilike(f"%{query}%"),
                        ExecutionLog.request_id.ilike(f"%{query}%"),
                    )
                ).limit(limit)
                logs = list((await s.execute(stmt)).scalars().all())
                filtered = bool(logs)

            if not logs:
                logs = list((await s.execute(base_query().limit(limit))).scalars().all())

            results = [
                {
                    "id": item.id,
                    "request_id": item.request_id,
                    "session_id": item.session_id,
                    "tool_name": item.tool_name,
                    "status": item.status,
                    "latency_ms": item.latency_ms,
                    "error_message": item.error_message,
                    "created_at": str(item.created_at),
                    "input_args": item.input_args,
                    "response_summary": item.response_payload,
                }
                for item in logs
            ]

            return {
                "scope": "execution_logs",
                "query": query,
                "keyword_filter_applied": filtered,
                "scoped_to_user": user_id if user_role != "admin" else "all_users",
                "count": len(results),
                "logs": results,
            }

    def as_registered_tool(self) -> RegisteredTool:
        """Expose System Search Tool as RegisteredTool for LLM binding."""
        return RegisteredTool(
            name="system_search_tool",
            domain="general",
            method="GET",
            path="/system/search",
            description=(
                "System Search Tool: Queries the system itself. Use 'capabilities' scope to find what tools "
                "are available (e.g. for invoicing, disputes, payments). Use 'execution_logs' scope to inspect "
                "the status and result of past requests or executions."
            ),
            keywords=["system search", "capabilities", "available tools", "status of last request", "logs", "audit"],
            parameters=[
                ParameterSchema(
                    name="scope",
                    type="string",
                    description="Scope to query: 'capabilities' for available tools, 'execution_logs' for past run status",
                    required=True,
                    enum=["capabilities", "execution_logs"],
                    location="query",
                ),
                ParameterSchema(
                    name="query",
                    type="string",
                    description="Keywords to filter tools (e.g. 'invoicing') or logs (e.g. 'order')",
                    required=False,
                    location="query",
                ),
                ParameterSchema(
                    name="limit",
                    type="number",
                    description="Maximum number of items to return (default 5)",
                    required=False,
                    location="query",
                ),
            ],
            is_mutating=False,
            source="meta_tool",
        )


_system_search_instance: Optional[SystemSearchTool] = None


def get_system_search_tool() -> SystemSearchTool:
    global _system_search_instance
    if _system_search_instance is None:
        _system_search_instance = SystemSearchTool()
    return _system_search_instance

