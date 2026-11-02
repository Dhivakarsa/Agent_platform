"""
Dual-index tool catalog service.

Synchronizes canonical tool definitions across relational metadata (PostgreSQL / SQLite)
and semantic vector representations (Qdrant).
Supports admin tool creation, schema validation, mock test runs, and status toggling.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional
from sqlalchemy import desc, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import ToolDefinition
from app.db.session import async_session_factory
from app.db.vector_store import VectorStoreService, get_vector_store
from app.services.embedding_service import get_embedding_service
from app.schemas.tool import (
    HttpMethod,
    ParameterSchema,
    RegisteredTool,
    ToolCreateRequest,
    ToolStatus,
    ToolUpdateRequest,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Maintains synchronization between relational metadata and Qdrant vector store."""

    def __init__(self, vector_store: Optional[VectorStoreService] = None) -> None:
        self.settings = get_settings()
        self.vector_store = vector_store or get_vector_store()
        self.embeddings = get_embedding_service()
        self._memory_cache: dict[str, RegisteredTool] = {}

    @staticmethod
    def _contract_columns(tool: RegisteredTool) -> dict[str, Any]:
        """
        Split a tool's contract across the columns that persist it.

        Parameters are stored by location (path/query/body), keywords in ``tags`` and
        prerequisite edges in ``dependencies``, so ``list_tools`` can rebuild the whole
        contract after a restart. Both write paths use this to stay consistent.
        """
        params_json = [p.model_dump() for p in tool.parameters]
        deps_json = [
            d.model_dump() if hasattr(d, "model_dump") else d
            for d in (tool.dependencies or [])
        ]

        body_params = [p for p in params_json if p.get("location") not in ("path", "query")]
        body_schema = dict(tool.request_body_schema or {})
        if body_params:
            body_schema.setdefault("params", body_params)

        existing_edges = {d.get("tool") for d in deps_json if isinstance(d, dict)}
        for prior in tool.requires_prior or []:
            if prior not in existing_edges:
                deps_json.append({"tool": prior})

        return {
            "path_params": [p for p in params_json if p.get("location") == "path"],
            "query_params": [p for p in params_json if p.get("location") == "query"],
            "request_body_schema": body_schema,
            "tags": list(tool.keywords or tool.tags or []),
            "dependencies": deps_json,
        }

    def _build_indexing_summary(self, tool: RegisteredTool) -> str:
        """Construct dense semantic summary representing tool capabilities for vectorization."""
        keywords_str = ", ".join(tool.keywords or tool.tags or [tool.name])
        priors = [d.tool if hasattr(d, "tool") else str(d) for d in (tool.dependencies or tool.requires_prior or [])]
        prior_str = ", ".join(priors) if priors else "None"
        p_val = getattr(tool, "path", None) or getattr(tool, "endpoint_path", "")
        return (
            f"Name: {tool.name}\n"
            f"Display Name: {tool.display_name or tool.name}\n"
            f"Domain: {tool.domain or tool.category}\n"
            f"Method: {tool.method} {p_val}\n"
            f"Description: {tool.description}\n"
            f"Keywords: {keywords_str}\n"
            f"Requires Prior: {prior_str}\n"
            f"Is Mutating: {tool.is_mutating}\n"
            f"Approval Required: {tool.approval_required}"
        )

    def _generate_embedding(self, text: str) -> list[float]:
        """Embed a tool summary through the shared provider."""
        return self.embeddings.embed(text)

    async def register_tool(self, tool: RegisteredTool, session: Optional[AsyncSession] = None) -> str:
        """Insert or update a tool into relational store and Qdrant index."""
        self._memory_cache[tool.name] = tool

        async def _save_sql(s: AsyncSession) -> None:
            stmt = select(ToolDefinition).where(ToolDefinition.name == tool.name)
            res = await s.execute(stmt)
            existing = res.scalar_one_or_none()

            contract = self._contract_columns(tool)

            if existing:
                existing.display_name = tool.display_name or tool.name
                existing.description = tool.description
                existing.category = tool.domain or tool.category or "general"
                existing.service = tool.service
                existing.status = tool.status
                existing.method = tool.method
                existing.endpoint_path = tool.path
                existing.is_mutating = tool.is_mutating
                existing.approval_required = tool.approval_required
                for column, value in contract.items():
                    setattr(existing, column, value)
                existing.risk_level = (
                    tool.risk_level.value if hasattr(tool.risk_level, "value") else str(tool.risk_level)
                )
                existing.namespace = tool.namespace
            else:
                row = ToolDefinition(
                    name=tool.name,
                    display_name=tool.display_name or tool.name,
                    description=tool.description,
                    category=tool.domain or tool.category or "general",
                    service=tool.service,
                    status=tool.status,
                    method=tool.method,
                    endpoint_path=tool.path,
                    is_mutating=tool.is_mutating,
                    approval_required=tool.approval_required,
                    **contract,
                    risk_level=(
                        tool.risk_level.value if hasattr(tool.risk_level, "value") else str(tool.risk_level)
                    ),
                    namespace=tool.namespace,
                )
                s.add(row)
            await s.commit()

        if session:
            await _save_sql(session)
        else:
            async with async_session_factory() as local_session:
                await _save_sql(local_session)

        # Upsert vector
        summary = self._build_indexing_summary(tool)
        vector = self._generate_embedding(summary)
        point_id = int(uuid.uuid5(uuid.NAMESPACE_DNS, tool.name).int % 0x7FFFFFFF)

        self.vector_store.upsert_vectors(
            collection_name=self.settings.qdrant_collection_name,
            points=[
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "name": tool.name,
                        "domain": tool.domain or tool.category,
                        "method": tool.method,
                        "is_mutating": tool.is_mutating,
                        "approval_required": tool.approval_required,
                        "status": tool.status,
                        "description": tool.description,
                    },
                }
            ],
        )
        return tool.name

    async def register_batch(self, tools: list[RegisteredTool]) -> int:
        """Batch register multiple tools into SQL and Qdrant."""
        logger.info("Batch registering %d tools...", len(tools))
        points = []
        for tool in tools:
            self._memory_cache[tool.name] = tool
            summary = self._build_indexing_summary(tool)
            vec = self._generate_embedding(summary)
            point_id = int(uuid.uuid5(uuid.NAMESPACE_DNS, tool.name).int % 0x7FFFFFFF)
            points.append(
                {
                    "id": point_id,
                    "vector": vec,
                    "payload": {
                        "name": tool.name,
                        "domain": tool.domain or tool.category,
                        "method": tool.method,
                        "is_mutating": tool.is_mutating,
                        "approval_required": tool.approval_required,
                        "status": tool.status,
                        "description": tool.description,
                    },
                }
            )

        self.vector_store.upsert_vectors(
            collection_name=self.settings.qdrant_collection_name,
            points=points,
        )

        async with async_session_factory() as s:
            for tool in tools:
                stmt = select(ToolDefinition).where(ToolDefinition.name == tool.name)
                res = await s.execute(stmt)
                existing = res.scalar_one_or_none()
                contract = self._contract_columns(tool)

                src_type = str(getattr(tool, "source_type", "http_api"))
                if hasattr(getattr(tool, "source_type", None), "value"):
                    src_type = tool.source_type.value
                risk_val = getattr(tool, "risk_level", "read_only")
                if hasattr(risk_val, "value"):
                    risk_val = risk_val.value

                if not existing:
                    s.add(
                        ToolDefinition(
                            name=tool.name,
                            display_name=tool.display_name or tool.name,
                            description=tool.description,
                            category=tool.domain or tool.category or "general",
                            namespace=getattr(tool, "namespace", tool.domain or "paypal"),
                            capabilities=getattr(tool, "capabilities", []),
                            risk_level=str(risk_val),
                            approval_policy=getattr(tool, "approval_policy", "standard"),
                            idempotency=getattr(tool, "idempotency", "standard"),
                            service=tool.service,
                            status=tool.status,
                            method=tool.method,
                            endpoint_path=tool.path,
                            source_type=src_type,
                            mcp_server_name=getattr(tool, "mcp_server_name", None),
                            remote_tool_name=getattr(tool, "remote_tool_name", None),
                            is_mutating=tool.is_mutating,
                            approval_required=tool.approval_required,
                            **contract,
                        )
                    )
                else:
                    existing.namespace = getattr(tool, "namespace", tool.domain or "paypal")
                    existing.capabilities = getattr(tool, "capabilities", [])
                    existing.risk_level = str(risk_val)
                    appr_pol = getattr(tool, "approval_policy", "standard")
                    if hasattr(appr_pol, "value"):
                        appr_pol = appr_pol.value
                    existing.approval_policy = str(appr_pol)
                    existing.approval_required = tool.approval_required
                    existing.is_mutating = tool.is_mutating
                    existing.endpoint_path = tool.path or existing.endpoint_path
                    for column, value in contract.items():
                        setattr(existing, column, value)
            await s.commit()

        return len(tools)

    async def create_admin_tool(self, req: ToolCreateRequest) -> ToolDefinition:
        """Create a new technical tool from the Admin Management screen."""
        async with async_session_factory() as s:
            # Check unique name
            stmt = select(ToolDefinition).where(ToolDefinition.name == req.name)
            if (await s.execute(stmt)).scalar_one_or_none():
                raise ValueError(f"Tool with name '{req.name}' already exists.")

            tool_row = ToolDefinition(
                name=req.name,
                display_name=req.display_name,
                description=req.description,
                category=req.category,
                service=req.service,
                tags=req.tags,
                version=req.version,
                status=req.status.value,
                api_type=req.api_type,
                method=req.method.value,
                base_url=req.base_url,
                endpoint_path=req.endpoint_path,
                content_type=req.content_type,
                timeout_seconds=req.timeout_seconds,
                auth_profile=req.auth_profile,
                path_params=[p.model_dump() for p in req.path_params],
                query_params=[q.model_dump() for q in req.query_params],
                headers=req.headers,
                request_body_schema=req.request_body_schema,
                response_schema=req.response_schema,
                is_mutating=req.is_mutating,
                approval_required=req.approval_required,
                permitted_roles=req.permitted_roles,
                idempotency_supported=req.idempotency_supported,
                retry_config=req.retry_config,
                dependencies=[d.model_dump() for d in req.dependencies],
            )
            s.add(tool_row)
            await s.commit()
            await s.refresh(tool_row)

        # Convert to RegisteredTool and index in Qdrant
        registered = RegisteredTool(
            tool_id=tool_row.tool_id,
            name=tool_row.name,
            display_name=tool_row.display_name,
            domain=tool_row.category,
            category=tool_row.category,
            service=tool_row.service,
            method=tool_row.method,
            path=tool_row.endpoint_path,
            description=tool_row.description,
            status=tool_row.status,
            is_mutating=tool_row.is_mutating,
            approval_required=tool_row.approval_required,
            dependencies=req.dependencies,
        )
        self._memory_cache[registered.name] = registered

        summary = self._build_indexing_summary(registered)
        vec = self._generate_embedding(summary)
        point_id = int(uuid.uuid5(uuid.NAMESPACE_DNS, registered.name).int % 0x7FFFFFFF)
        self.vector_store.upsert_vectors(
            collection_name=self.settings.qdrant_collection_name,
            points=[{
                "id": point_id,
                "vector": vec,
                "payload": {
                    "name": registered.name,
                    "domain": registered.domain,
                    "method": registered.method,
                    "is_mutating": registered.is_mutating,
                    "approval_required": registered.approval_required,
                    "status": registered.status,
                    "description": registered.description,
                },
            }],
        )
        return tool_row

    async def update_tool(self, tool_id: str, req: ToolUpdateRequest) -> Optional[ToolDefinition]:
        """Update an existing tool configuration."""
        async with async_session_factory() as s:
            stmt = select(ToolDefinition).where(ToolDefinition.tool_id == tool_id)
            res = await s.execute(stmt)
            tool = res.scalar_one_or_none()
            if not tool:
                return None

            if req.display_name is not None:
                tool.display_name = req.display_name
            if req.description is not None:
                tool.description = req.description
            if req.category is not None:
                tool.category = req.category
            if req.status is not None:
                tool.status = req.status.value
            if req.timeout_seconds is not None:
                tool.timeout_seconds = req.timeout_seconds
            if req.is_mutating is not None:
                tool.is_mutating = req.is_mutating
            if req.approval_required is not None:
                tool.approval_required = req.approval_required
            if req.permitted_roles is not None:
                tool.permitted_roles = req.permitted_roles
            if req.request_body_schema is not None:
                tool.request_body_schema = req.request_body_schema
            if req.response_schema is not None:
                tool.response_schema = req.response_schema
            if req.dependencies is not None:
                tool.dependencies = [d.model_dump() for d in req.dependencies]

            await s.commit()
            await s.refresh(tool)
            return tool

    async def patch_status(self, tool_id: str, status: ToolStatus) -> Optional[ToolDefinition]:
        """Toggle tool active status."""
        async with async_session_factory() as s:
            stmt = select(ToolDefinition).where(ToolDefinition.tool_id == tool_id)
            tool = (await s.execute(stmt)).scalar_one_or_none()
            if tool:
                tool.status = status.value
                await s.commit()
                await s.refresh(tool)
                if tool.name in self._memory_cache:
                    self._memory_cache[tool.name].status = status.value
            return tool

    async def get_tool_by_id(self, tool_id: str) -> Optional[ToolDefinition]:
        """Lookup by internal UUID."""
        async with async_session_factory() as s:
            stmt = select(ToolDefinition).where(ToolDefinition.tool_id == tool_id)
            return (await s.execute(stmt)).scalar_one_or_none()

    def get_tool(self, name: str) -> Optional[RegisteredTool]:
        """Lookup by snake_case name."""
        return self._memory_cache.get(name)

    async def list_tools(self, domain: Optional[str] = None, only_enabled: bool = False) -> list[RegisteredTool]:
        """List registered tools from SQL."""
        async with async_session_factory() as s:
            stmt = select(ToolDefinition).order_by(ToolDefinition.name)
            if domain:
                stmt = stmt.where(ToolDefinition.category == domain)
            if only_enabled:
                stmt = stmt.where(ToolDefinition.status == "enabled")
            res = await s.execute(stmt)
            rows = res.scalars().all()

            results = []
            for r in rows:
                t = RegisteredTool(
                    id=str(r.id),
                    tool_id=r.tool_id,
                    name=r.name,
                    display_name=r.display_name or r.name,
                    domain=r.category,
                    category=r.category,
                    namespace=getattr(r, "namespace", r.category or "paypal"),
                    capabilities=getattr(r, "capabilities", []) or [],
                    service=r.service,
                    method=r.method,
                    path=r.endpoint_path,
                    description=r.description,
                    version=r.version,
                    status=r.status,
                    source_type=getattr(r, "source_type", "http_api"),
                    mcp_server_name=getattr(r, "mcp_server_name", None),
                    remote_tool_name=getattr(r, "remote_tool_name", None),
                    risk_level=getattr(r, "risk_level", "read_only"),
                    approval_policy=getattr(r, "approval_policy", "standard"),
                    idempotency=getattr(r, "idempotency", "standard"),
                    is_mutating=r.is_mutating,
                    approval_required=r.approval_required,
                    request_body_schema=r.request_body_schema or {},
                    response_schema=r.response_schema or {},
                    parameters=self._rehydrate_parameters(r),
                    keywords=list(r.tags or []),
                    tags=list(r.tags or []),
                    requires_prior=[
                        d.get("tool")
                        for d in (r.dependencies or [])
                        if isinstance(d, dict) and d.get("tool")
                    ],
                )
                # A cached entry built at registration time is at least as complete as
                # this row, so never replace it with a reconstruction.
                self._memory_cache.setdefault(r.name, t)
                results.append(self._memory_cache.get(r.name, t))
            return results

    @staticmethod
    def _rehydrate_parameters(row: ToolDefinition) -> list[ParameterSchema]:
        """Reassemble the parameter list from the columns register_tool wrote it to."""
        raw: list[Any] = []
        raw.extend(row.path_params or [])
        raw.extend(row.query_params or [])
        raw.extend((row.request_body_schema or {}).get("params", []) or [])

        params: list[ParameterSchema] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if item["name"] in seen:
                continue
            seen.add(item["name"])
            try:
                params.append(ParameterSchema(**item))
            except Exception:
                logger.debug("Skipping malformed parameter %r on tool %s", item, row.name)
        return params


    def get_tool_count(self) -> int:
        return len(self._memory_cache)

    async def search_by_metadata(self, query: str, domain: Optional[str] = None, limit: int = 10) -> list[RegisteredTool]:
        """SQL text query across tool catalog."""
        query_pattern = f"%{query}%"
        async with async_session_factory() as s:
            stmt = select(ToolDefinition).where(
                or_(
                    ToolDefinition.name.ilike(query_pattern),
                    ToolDefinition.display_name.ilike(query_pattern),
                    ToolDefinition.description.ilike(query_pattern),
                    ToolDefinition.category.ilike(query_pattern),
                )
            )
            if domain:
                stmt = stmt.where(ToolDefinition.category == domain)
            stmt = stmt.limit(limit)
            res = await s.execute(stmt)
            rows = res.scalars().all()

            results = []
            for r in rows:
                t = RegisteredTool(
                    id=str(r.id),
                    tool_id=r.tool_id,
                    name=r.name,
                    display_name=r.display_name or r.name,
                    domain=r.category,
                    category=r.category,
                    service=r.service,
                    method=r.method,
                    path=r.endpoint_path,
                    description=r.description,
                    status=r.status,
                    is_mutating=r.is_mutating,
                    approval_required=r.approval_required,
                )
                self._memory_cache[r.name] = t
                results.append(t)
            return results


_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry
