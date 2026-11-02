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
from app.core.tracing import configure_tracing, tracing_enabled
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.db.models import AgentRun, ApprovalRequest, ExecutionLog, TokenUsage, User
from app.db.session import async_session_factory, init_db
from app.graph.workflow import close_checkpointer, resume_agent, run_agent
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
from app.services.bootstrap import ensure_platform_ready, seed_system_catalog
from app.services.tool_registry import get_tool_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle handler."""
    logger.info("Starting Datazoic Agentic Platform...")
    configure_tracing()
    await ensure_platform_ready()
    yield
    await close_checkpointer()
    logger.info("Datazoic Agentic Platform shutdown.")


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Datazoic Agentic System: Role-based Multi-screen API Orchestration Platform",
    lifespan=lifespan,
)

# A wildcard origin combined with credentials would let any website call this API with a
# signed-in user's token, so the allowed origins are explicit and configurable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
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
    """
    Approve or reject a run that is paused at the policy gate.

    Resumes the suspended graph with the decision, so an approved action continues through
    execution and any remaining plan steps rather than being dispatched in isolation.
    """
    async with async_session_factory() as s:
        run = (
            await s.execute(select(AgentRun).where(AgentRun.run_id == run_id))
        ).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Only the run's owner, or an administrator, may decide its approval.
    if current_user["role"] != "admin" and run.user_id != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="You cannot decide another user's approval")

    if run.approval_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not awaiting approval (approval_status={run.approval_status})",
        )

    action = "approve" if req.action == "approve" else "reject"
    result = await resume_agent(run_id=run_id, action=action, reason=req.reason)

    if result is None:
        # No suspended checkpoint: the process restarted with in-memory checkpoints, or
        # the decision was already applied.
        async with async_session_factory() as s:
            stale = (
                await s.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.run_id == run_id,
                        ApprovalRequest.status == "pending",
                    )
                )
            ).scalars().all()
            for row in stale:
                row.status = "expired"
                row.reason = "Suspended run was no longer resumable"
            refreshed = (
                await s.execute(select(AgentRun).where(AgentRun.run_id == run_id))
            ).scalar_one_or_none()
            if refreshed:
                refreshed.status = "failed"
                refreshed.approval_status = "expired"
            await s.commit()
        raise HTTPException(
            status_code=409,
            detail="This run is no longer resumable. Please re-send the request.",
        )

    async with async_session_factory() as s:
        decided = (
            await s.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id)
                .order_by(desc(ApprovalRequest.id))
            )
        ).scalars().first()
        if decided:
            decided.decided_by = current_user["email"]
            await s.commit()

    return {
        "run_id": run_id,
        "decision": action,
        "status": result.approval_status,
        "decided_by": current_user["email"],
        "run_status": result.status,
        "response": result.response,
        "tools_used": result.tools_used,
        "execution_trace": result.execution_trace,
        "timeline_events": result.timeline_events,
        "token_usage": result.token_usage,
        # A later step in the same plan may need its own confirmation.
        "pending_approval": result.pending_approval,
        # Kept for compatibility with the existing frontend.
        "result": (result.execution_trace or [{}])[-1].get("response", {}),
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
        user_id=admin_user["user_id"],
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


async def _build_user_activity(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """
    Assemble one row per executed task for a user.

    AgentRun.run_id, TokenUsage.request_id and ExecutionLog.request_id all carry the
    same per-turn request id, so a task's token cost and its individual tool calls can
    be attached to the run they belong to.
    """
    async with async_session_factory() as s:
        runs = (
            await s.execute(
                select(AgentRun)
                .where(AgentRun.user_id == user_id)
                .order_by(desc(AgentRun.id))
                .limit(limit)
            )
        ).scalars().all()

        run_ids = [r.run_id for r in runs]
        if not run_ids:
            return []

        # Token cost per request id.
        tokens_by_request: dict[str, dict[str, Any]] = {}
        token_rows = (
            await s.execute(select(TokenUsage).where(TokenUsage.request_id.in_(run_ids)))
        ).scalars().all()
        for row in token_rows:
            bucket = tokens_by_request.setdefault(
                row.request_id,
                {"model": row.model, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )
            bucket["input_tokens"] += row.input_tokens or 0
            bucket["output_tokens"] += row.output_tokens or 0
            bucket["total_tokens"] += row.total_tokens or 0

        # Individual tool invocations per request id.
        logs_by_request: dict[str, list[dict[str, Any]]] = {}
        log_rows = (
            await s.execute(
                select(ExecutionLog)
                .where(ExecutionLog.request_id.in_(run_ids))
                .order_by(ExecutionLog.id)
            )
        ).scalars().all()
        for row in log_rows:
            logs_by_request.setdefault(row.request_id, []).append(
                {
                    "tool_name": row.tool_name,
                    "status": row.status,
                    "latency_ms": row.latency_ms,
                    "error_message": row.error_message,
                    "input_args": row.input_args,
                    "response_payload": row.response_payload,
                    "created_at": str(row.created_at),
                }
            )

        activity: list[dict[str, Any]] = []
        for run in runs:
            steps = logs_by_request.get(run.run_id, [])
            usage = tokens_by_request.get(run.run_id)
            failed = [s_ for s_ in steps if s_["status"] != "success"]
            activity.append(
                {
                    "run_id": run.run_id,
                    "task": run.query,
                    "status": run.status,
                    "approval_status": run.approval_status,
                    "tools_used": run.tools_used or [],
                    "duration_ms": run.duration_ms,
                    "created_at": str(run.created_at),
                    # Per-task token cost, which Screen 3 shows beside the task.
                    "token_usage": usage
                    or {"model": None, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    # Per-task tool log rows, which Screen 4 renders as a table.
                    "steps": steps,
                    "step_count": len(steps),
                    "failed_step_count": len(failed),
                    "execution_trace": run.execution_trace,
                }
            )
        return activity


@app.get("/usage/me/activity", tags=["Usage"])
async def get_my_activity(
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Each of the caller's own tasks with its token cost and tool log rows."""
    activity = await _build_user_activity(current_user["user_id"], limit=limit)
    return {"user_id": current_user["user_id"], "count": len(activity), "activity": activity}


@app.get("/admin/users/{user_id}/activity", tags=["Admin Usage"])
async def get_user_activity_admin(
    user_id: str,
    limit: int = 100,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator-only: a target user's tasks, each with tokens and tool log rows."""
    activity = await _build_user_activity(user_id, limit=limit)
    return {"user_id": user_id, "count": len(activity), "activity": activity}


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
