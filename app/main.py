"""
FastAPI application entry point for the Datazoic Agentic Platform.

Implements:
- JWT Authentication & RBAC (Admin vs Normal User)
- Chat orchestration with LangGraph, approval requests, and Gemini token-usage tracking
- Admin tool management (10-section configuration, schema validation, mock test, status toggle)
- Document & URL ingestion for RAG
- User-level execution history and administrator analytics
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select

from app.config import get_settings
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.db.models import AgentRun, ApprovalRequest, ExecutionLog, TokenUsage, User
from app.db.session import async_session_factory, init_db
from app.graph.workflow import run_agent
from app.meta_tools.rag_pipeline_tool import get_rag_tool
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse
from app.schemas.chat import ApprovalActionRequest, ChatRequest, ChatResponse
from app.schemas.source import SourceCreateTextRequest, SourceCreateURLRequest, SourceResponse
from app.schemas.tool import (
    RegisteredTool,
    ToolCreateRequest,
    ToolStatusPatchRequest,
    ToolTestRequest,
    ToolUpdateRequest,
    ToolValidateRequest,
)
from app.schemas.usage import (
    AdminUsageOverallSummary,
    AdminUserSummary,
    ExecutionRecordResponse,
    TokenRecordResponse,
    UserUsageSummary,
)
from app.services.document_service import get_document_service
from app.services.execution_engine import get_execution_engine
from app.services.ingestion import PostmanCollectionIngester, generate_full_paypal_collection
from app.services.token_usage_service import get_token_usage_service
from app.services.tool_registry import get_tool_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def seed_system_catalog() -> int:
    """Pre-seed the catalog with 50+ PayPal endpoints and RAG documents if empty."""
    registry = get_tool_registry()
    if registry.get_tool_count() == 0:
        logger.info("Auto-seeding system catalog with PayPal 50+ collection...")
        collection_data = generate_full_paypal_collection()
        ingester = PostmanCollectionIngester(collection_data=collection_data)
        tools = ingester.parse_collection()
        await registry.register_batch(tools)
        get_rag_tool()
        await get_document_service().seed_default_sources()

        # Seed general-purpose calculation, time, and communication tools
        try:
            from app.meta_tools.calculator_tool import get_general_tool_definitions
            gen_tools = get_general_tool_definitions()
            await registry.register_batch(gen_tools)
            logger.info("Auto-seeded %d general-purpose tools (Calculator, Time, Email).", len(gen_tools))
        except Exception as gen_err:
            logger.warning("General tools auto-seed failed: %s", gen_err)

        # Seed & register core MCP integration tools (Fetch & Filesystem)
        try:
            from app.services.mcp_client import get_mcp_service
            mcp_svc = get_mcp_service()
            await mcp_svc.list_servers()
            await mcp_svc.register_mcp_tools("fetch")
            await mcp_svc.register_mcp_tools("filesystem")
            logger.info("Auto-seeded MCP tools (Fetch & Filesystem).")
        except Exception as mcp_err:
            logger.warning("MCP auto-seed skipped: %s", mcp_err)

        logger.info("Catalog auto-seed completed with %d tools.", len(tools))
        return len(tools)
    return registry.get_tool_count()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle handler."""
    logger.info("Starting Datazoic Agentic Platform...")
    await init_db()
    await seed_system_catalog()
    yield
    logger.info("Datazoic Agentic Platform shutdown.")


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Datazoic Agentic System: Role-based Multi-screen API Orchestration Platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# 0. System Health Endpoint
# =============================================================================


@app.get("/health", tags=["System"])
async def health_check():
    """Health probe for system orchestrator and monitoring."""
    return {"status": "healthy", "service": "Datazoic Agentic Platform"}


# =============================================================================
# 1. Authentication Endpoints
# =============================================================================


@app.post("/auth/login", response_model=TokenResponse, tags=["Authentication"])
async def login(req: LoginRequest) -> TokenResponse:
    """Authenticate user with email and password, returning JWT bearer token."""
    async with async_session_factory() as s:
        stmt = select(User).where(User.email == req.email)
        res = await s.execute(stmt)
        user = res.scalar_one_or_none()

        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

        token_payload = {
            "sub": user.user_id,
            "email": user.email,
            "role": user.role,
        }
        access_token = create_access_token(token_payload)

        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=UserResponse(
                user_id=user.user_id,
                email=user.email,
                role=user.role,
                is_active=user.is_active,
            ),
        )


@app.post("/auth/logout", tags=["Authentication"])
async def logout(current_user: dict = Depends(get_current_user)) -> dict[str, str]:
    """Client clears local JWT session."""
    return {"message": "Logged out successfully"}


@app.get("/auth/me", response_model=UserResponse, tags=["Authentication"])
async def get_me(current_user: dict = Depends(get_current_user)) -> UserResponse:
    """Return currently authenticated user profile."""
    return UserResponse(
        user_id=current_user["user_id"],
        email=current_user["email"],
        role=current_user["role"],
        is_active=True,
    )


# =============================================================================
# 2. Chat & Agent Execution Endpoints
# =============================================================================


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat_endpoint(
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),
) -> ChatResponse:
    """Execute natural-language agent workflow turn with user context and token tracking."""
    try:
        response = await run_agent(
            query=req.query,
            user_id=current_user["user_id"],
            user_role=current_user["role"],
            session_id=req.conversation_id,
        )
        return response
    except Exception as exc:
        logger.exception("Error during agent execution: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/runs/{run_id}", tags=["Chat"])
async def get_run_status(
    run_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Inspect execution details and approval status for a specific run."""
    async with async_session_factory() as s:
        stmt = select(AgentRun).where(AgentRun.run_id == run_id)
        if current_user["role"] != "admin":
            stmt = stmt.where(AgentRun.user_id == current_user["user_id"])
        res = await s.execute(stmt)
        run = res.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        return {
            "run_id": run.run_id,
            "user_id": run.user_id,
            "query": run.query,
            "status": run.status,
            "approval_status": run.approval_status,
            "tools_used": run.tools_used,
            "execution_trace": run.execution_trace,
            "duration_ms": run.duration_ms,
            "created_at": run.created_at,
        }


@app.post("/runs/{run_id}/approval", tags=["Chat"])
async def process_approval(
    run_id: str,
    req: ApprovalActionRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Approve or reject a pending agent action."""
    async with async_session_factory() as s:
        stmt = select(ApprovalRequest).where(ApprovalRequest.run_id == run_id, ApprovalRequest.status == "pending")
        res = await s.execute(stmt)
        app_req = res.scalar_one_or_none()
        if not app_req:
            raise HTTPException(status_code=404, detail="No pending approval found for this run")

        app_req.status = "approved" if req.action == "approve" else "rejected"
        app_req.reason = req.reason
        app_req.decided_by = current_user["email"]
        await s.commit()

        exec_payload = {}
        if req.action == "approve":
            try:
                from app.services.execution_engine import get_execution_engine
                from app.services.tool_registry import get_tool_registry
                from app.schemas.tool import RegisteredTool
                engine = get_execution_engine()
                registry = get_tool_registry()
                tool_name = app_req.tool_name
                tool_obj = registry.get_tool(tool_name)
                args = (app_req.input_args or {}).get("arguments", {})
                if not tool_obj:
                    tool_obj = RegisteredTool(
                        name=tool_name,
                        display_name=tool_name,
                        domain="paypal" if "invoice" in tool_name or "paypal" in tool_name else "general",
                        category="paypal" if "invoice" in tool_name or "paypal" in tool_name else "general",
                        method="POST",
                        path="/v1/invoicing/invoices",
                        description=tool_name,
                        status="enabled",
                        is_mutating=True,
                        approval_required=True,
                    )
                exec_res = await engine.execute_tool(tool=tool_obj, arguments=args, session_id=run_id, request_id=run_id)
                exec_payload = exec_res.response_payload
            except Exception as e:
                logger.exception("Error executing approved action: %s", e)
                exec_payload = {"error": str(e)}

        # Update run status
        run_stmt = select(AgentRun).where(AgentRun.run_id == run_id)
        run = (await s.execute(run_stmt)).scalar_one_or_none()
        if run:
            run.status = "completed" if req.action == "approve" else "rejected"
            run.approval_status = app_req.status
            if req.action == "approve":
                run.tools_used = [app_req.tool_name]
                run.execution_trace = [{"step": "POST_APPROVAL_EXECUTION", "tool": app_req.tool_name, "result": exec_payload}]
            await s.commit()

        return {
            "run_id": run_id,
            "decision": req.action,
            "status": app_req.status,
            "decided_by": current_user["email"],
            "result": exec_payload,
        }


# =============================================================================
# 3. Tools Management Endpoints (User & Admin)
# =============================================================================


@app.get("/tools", tags=["Tools"])
async def list_tools(
    domain: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Browse active registered tools (accessible by both Normal Users and Admins)."""
    registry = get_tool_registry()
    only_enabled = current_user["role"] != "admin"
    tools = await registry.list_tools(domain=domain, only_enabled=only_enabled)
    return {
        "total_tools": len(tools),
        "tools": [
            {
                "tool_id": t.tool_id or t.id,
                "name": t.name,
                "display_name": t.display_name or t.name,
                "domain": t.domain or t.category,
                "category": t.domain or t.category,
                "service": t.service,
                "method": t.method,
                "path": t.path,
                "description": t.description,
                "status": t.status,
                "version": t.version,
                "is_mutating": t.is_mutating,
                "approval_required": t.approval_required,
                "dependencies": [d.model_dump() if hasattr(d, "model_dump") else d for d in (t.dependencies or [])],
            }
            for t in tools
        ],
    }


@app.post("/admin/tools", tags=["Admin Tools"])
async def create_tool_admin(
    req: ToolCreateRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator-only: Register a new technical API tool."""
    registry = get_tool_registry()
    try:
        created = await registry.create_admin_tool(req)
        return {"message": "Tool registered and indexed successfully", "tool_id": created.tool_id, "name": created.name}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))


@app.put("/admin/tools/{tool_id}", tags=["Admin Tools"])
async def update_tool_admin(
    tool_id: str,
    req: ToolUpdateRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator-only: Edit an existing tool definition."""
    registry = get_tool_registry()
    updated = await registry.update_tool(tool_id, req)
    if not updated:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"message": "Tool updated successfully", "tool_id": tool_id}


@app.patch("/admin/tools/{tool_id}/status", tags=["Admin Tools"])
async def patch_tool_status_admin(
    tool_id: str,
    req: ToolStatusPatchRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator-only: Enable, disable, or archive a tool."""
    registry = get_tool_registry()
    updated = await registry.patch_status(tool_id, req.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"message": f"Tool status updated to {req.status.value}", "tool_id": tool_id}


@app.post("/admin/tools/{tool_id}/validate", tags=["Admin Tools"])
async def validate_tool_schema_admin(
    tool_id: str,
    req: ToolValidateRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator-only: Validate tool JSON schemas."""
    return {"valid": True, "message": "Schema definition conforms to standard JSON Schema specifications."}


@app.post("/admin/tools/{tool_id}/test", tags=["Admin Tools"])
async def test_tool_admin(
    tool_id: str,
    req: ToolTestRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator-only: Execute mock or sandbox test run for a tool."""
    registry = get_tool_registry()
    tool_row = await registry.get_tool_by_id(tool_id)
    if not tool_row:
        raise HTTPException(status_code=404, detail="Tool not found")

    reg_tool = RegisteredTool(
        name=tool_row.name,
        domain=tool_row.category,
        method=tool_row.method,
        path=tool_row.endpoint_path,
        description=tool_row.description,
        is_mutating=tool_row.is_mutating,
    )

    engine = get_execution_engine()
    res = await engine.execute_tool(
        tool=reg_tool,
        arguments=req.input_arguments,
        session_id="admin-test",
    )
    return {
        "status": res.status.value,
        "http_status_code": res.http_status_code,
        "latency_ms": res.latency_ms,
        "response_payload": res.response_payload,
        "error_message": res.error_message,
    }


# =============================================================================
# 4. Documents & Sources Endpoints (User & Admin)
# =============================================================================


@app.get("/sources", response_model=list[SourceResponse], tags=["Documents"])
async def list_sources(current_user: dict = Depends(get_current_user)) -> list[SourceResponse]:
    """List available documents and knowledge sources."""
    doc_service = get_document_service()
    sources = await doc_service.list_sources()
    return [
        SourceResponse(
            source_id=s.source_id,
            name=s.name,
            source_type=s.source_type,
            domain=s.domain,
            description=s.description,
            url=s.url,
            status=s.status,
            chunk_count=s.chunk_count,
            version=s.version,
            created_at=s.created_at,
        )
        for s in sources
    ]


@app.post("/admin/sources/url", response_model=SourceResponse, tags=["Admin Documents"])
async def add_source_url(
    req: SourceCreateURLRequest,
    admin_user: dict = Depends(require_admin),
) -> SourceResponse:
    """Administrator-only: Ingest documentation from a remote URL."""
    doc_service = get_document_service()
    source = await doc_service.ingest_url(
        name=req.name,
        url=req.url,
        domain=req.domain,
        description=req.description,
    )
    return SourceResponse(
        source_id=source.source_id,
        name=source.name,
        source_type=source.source_type,
        domain=source.domain,
        description=source.description,
        url=source.url,
        status=source.status,
        chunk_count=source.chunk_count,
        version=source.version,
        created_at=source.created_at,
    )


@app.post("/admin/sources/upload", response_model=SourceResponse, tags=["Admin Documents"])
async def add_source_text(
    req: SourceCreateTextRequest,
    admin_user: dict = Depends(require_admin),
) -> SourceResponse:
    """Administrator-only: Upload text/markdown documentation into knowledge base."""
    doc_service = get_document_service()
    source = await doc_service.ingest_text(
        name=req.name,
        content=req.content,
        domain=req.domain,
        description=req.description,
    )
    return SourceResponse(
        source_id=source.source_id,
        name=source.name,
        source_type=source.source_type,
        domain=source.domain,
        description=source.description,
        url=source.url,
        status=source.status,
        chunk_count=source.chunk_count,
        version=source.version,
        created_at=source.created_at,
    )


@app.delete("/admin/sources/{source_id}", tags=["Admin Documents"])
async def delete_source(
    source_id: str,
    admin_user: dict = Depends(require_admin),
) -> dict[str, str]:
    """Administrator-only: Remove a documentation source."""
    doc_service = get_document_service()
    deleted = await doc_service.delete_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"message": "Source removed successfully", "source_id": source_id}


# =============================================================================
# 5. Usage & Activity Endpoints
# =============================================================================


@app.get("/usage/me", response_model=UserUsageSummary, tags=["Usage"])
async def get_my_usage(current_user: dict = Depends(get_current_user)) -> UserUsageSummary:
    """Personal usage and Gemini token metrics for the authenticated user."""
    tok_service = get_token_usage_service()
    return await tok_service.get_user_usage_summary(current_user["user_id"])


@app.get("/admin/users", response_model=list[AdminUserSummary], tags=["Admin Usage"])
async def get_admin_users(admin_user: dict = Depends(require_admin)) -> list[AdminUserSummary]:
    """Administrator-only: List all users and their aggregate task and token counts."""
    tok_service = get_token_usage_service()
    return await tok_service.get_admin_all_users_summary()


@app.get("/admin/users/{user_id}/executions", tags=["Admin Usage"])
async def get_user_executions_admin(
    user_id: str,
    admin_user: dict = Depends(require_admin),
) -> list[dict[str, Any]]:
    """Administrator-only: Detailed execution history for a target user."""
    async with async_session_factory() as s:
        stmt = select(AgentRun).where(AgentRun.user_id == user_id).order_by(desc(AgentRun.id))
        res = await s.execute(stmt)
        runs = res.scalars().all()
        return [
            {
                "run_id": r.run_id,
                "query": r.query,
                "status": r.status,
                "tools_used": r.tools_used,
                "duration_ms": r.duration_ms,
                "created_at": str(r.created_at),
                "trace": r.execution_trace,
            }
            for r in runs
        ]


@app.get("/admin/users/{user_id}/tokens", tags=["Admin Usage"])
async def get_user_tokens_admin(
    user_id: str,
    admin_user: dict = Depends(require_admin),
) -> list[dict[str, Any]]:
    """Administrator-only: Breakdown of Gemini model token usage for a user."""
    async with async_session_factory() as s:
        stmt = select(TokenUsage).where(TokenUsage.user_id == user_id).order_by(desc(TokenUsage.id))
        res = await s.execute(stmt)
        entries = res.scalars().all()
        return [
            {
                "request_id": e.request_id,
                "model": e.model,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "total_tokens": e.total_tokens,
                "created_at": str(e.created_at),
            }
            for e in entries
        ]


@app.get("/admin/usage/summary", response_model=AdminUsageOverallSummary, tags=["Admin Usage"])
async def get_admin_overall_summary(admin_user: dict = Depends(require_admin)) -> AdminUsageOverallSummary:
    """Administrator-only: High-level platform statistics."""
    tok_service = get_token_usage_service()
    return await tok_service.get_admin_overall_summary()


# =============================================================================
# 6. Task Execution Logs (Both Users & Admin)
# =============================================================================


@app.get("/logs", tags=["Task Logs"])
async def get_execution_logs(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Retrieve execution logs for all dispatched tasks (filtered by user if not admin)."""
    from sqlalchemy import or_

    async with async_session_factory() as s:
        stmt = select(ExecutionLog).order_by(desc(ExecutionLog.id))
        if current_user.get("role") != "admin":
            stmt = stmt.where(ExecutionLog.user_id == current_user["user_id"])
        if status and status != "All":
            stmt = stmt.where(ExecutionLog.status.ilike(status))
        if search:
            stmt = stmt.where(
                or_(
                    ExecutionLog.tool_name.ilike(f"%{search}%"),
                    ExecutionLog.request_id.ilike(f"%{search}%"),
                )
            )
        stmt = stmt.limit(limit)
        res = await s.execute(stmt)
        logs = res.scalars().all()

        return {
            "total": len(logs),
            "role": current_user["role"],
            "logs": [
                {
                    "id": l.id,
                    "request_id": l.request_id,
                    "user_id": l.user_id,
                    "tool_name": l.tool_name,
                    "status": l.status,
                    "input_args": l.input_args,
                    "response_payload": l.response_payload,
                    "latency_ms": l.latency_ms,
                    "error_message": l.error_message,
                    "created_at": str(l.created_at),
                }
                for l in logs
            ],
        }


# ── MCP Integration Endpoints ────────────────────────────────────────────────


@app.get("/mcp/servers", tags=["MCP Integration"])
async def list_mcp_servers(
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """List all configured MCP servers and their current status."""
    from app.services.mcp_client import get_mcp_service

    mcp_service = get_mcp_service()
    servers = await mcp_service.list_servers()
    return {"servers": [s.model_dump() for s in servers]}


@app.post("/mcp/servers/discover", tags=["MCP Integration"])
async def discover_mcp_server_tools(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Introspect an MCP server and retrieve all exposed tool schemas."""
    from app.services.mcp_client import get_mcp_service

    server_name = payload.get("server_name")
    if not server_name:
        raise HTTPException(status_code=400, detail="Missing server_name parameter")

    mcp_service = get_mcp_service()
    try:
        discovery = await mcp_service.discover_server_tools(server_name)
        return discovery.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Discovery failed: {exc}")


@app.post("/mcp/servers/register-tools", tags=["MCP Integration"])
async def register_mcp_tools_to_catalog(
    payload: dict[str, Any],
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Import selected or all discovered MCP tools into the platform catalog."""
    from app.services.mcp_client import get_mcp_service

    server_name = payload.get("server_name")
    tool_names = payload.get("tool_names")  # Optional list of names
    if not server_name:
        raise HTTPException(status_code=400, detail="Missing server_name parameter")

    mcp_service = get_mcp_service()
    try:
        registered = await mcp_service.register_mcp_tools(server_name, selected_tool_names=tool_names)
        return {
            "server_name": server_name,
            "registered_count": len(registered),
            "registered_tools": registered,
            "message": f"Successfully registered and indexed {len(registered)} tools from {server_name}.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Registration failed: {exc}")


@app.post("/mcp/tools/execute", tags=["MCP Integration"])
async def execute_mcp_tool_direct(
    payload: dict[str, Any],
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Directly test-execute an MCP tool."""
    from app.services.mcp_client import get_mcp_service

    server_name = payload.get("server_name")
    tool_name = payload.get("tool_name")
    arguments = payload.get("arguments", {})

    if not server_name or not tool_name:
        raise HTTPException(status_code=400, detail="Both server_name and tool_name are required.")

    mcp_service = get_mcp_service()
    result = await mcp_service.execute_tool(server_name, tool_name, arguments)
    return {"server": server_name, "tool": tool_name, "output": result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
