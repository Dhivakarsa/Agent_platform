"""
SQLAlchemy ORM models for the Datazoic Agentic System.

Tables:
- User & Role management (local hashed authentication)
- ToolDefinition (canonical technical metadata, schemas, DAG dependencies, approval flags)
- KnowledgeSource (document & URL RAG registry)
- Conversation & AgentRun (persisted chat sessions and execution state)
- ExecutionLog (immutable API audit ledger)
- TokenUsage (Gemini model token consumption per user & request)
- ApprovalRequest (human-in-the-loop pause/resume for mutating tools)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    type_annotation_map = {
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


def _utcnow() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


# ── 1. User & Authentication ─────────────────────────────────────────────────


class User(Base):
    """User account model with salted password hash and role-based permissions."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")  # "admin" or "user"
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_active: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<User(email={self.email!r}, role={self.role!r})>"


# ── 2. Tool Registry (Technical API Metadata) ────────────────────────────────


class ToolDefinition(Base):
    """Canonical registry for registered API tools with full technical configuration."""
    __tablename__ = "tool_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(128), nullable=False, default="PayPal")
    tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="enabled")  # draft, enabled, disabled, archived

    # API Configuration
    api_type: Mapped[str] = mapped_column(String(32), nullable=False, default="REST")
    method: Mapped[str] = mapped_column(String(10), nullable=False, default="GET")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, default="https://api-m.sandbox.paypal.com")
    endpoint_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="application/json")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    auth_profile: Mapped[str] = mapped_column(String(64), nullable=False, default="PAYPAL_SANDBOX")

    # Parameters & Schemas
    path_params: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    query_params: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    request_body_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Execution Behavior & Governance
    is_mutating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="read_only")
    approval_policy: Mapped[str] = mapped_column(String(64), nullable=False, default="standard")
    idempotency: Mapped[str] = mapped_column(String(64), nullable=False, default="standard")
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="paypal", index=True)
    capabilities: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    permitted_roles: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=lambda: ["admin", "user"])
    idempotency_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retry_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=lambda: {"max_retries": 2, "backoff": 1.0})

    # Dependencies DAG mapping
    dependencies: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # Source & MCP Attribution
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="http_api")
    mcp_server_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    remote_tool_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ── 2b. MCP Server Registry ──────────────────────────────────────────────────


class MCPServerDefinition(Base):
    """Registry of configured MCP servers (builtin, stdio, sse)."""
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    server_type: Mapped[str] = mapped_column(String(32), nullable=False, default="builtin")  # "builtin", "stdio", "sse"
    command: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    args: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    env_vars: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    discovered_tools_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ── 3. Knowledge Sources (Document & URL RAG Registry) ───────────────────────


class KnowledgeSource(Base):
    """Registry of uploaded documents and URLs indexed in the Qdrant knowledge base."""
    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")  # pdf, docx, url, text, api_doc
    domain: Mapped[str] = mapped_column(String(128), nullable=False, default="general")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="indexed")  # processing, indexed, failed
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ── 4. Chat & Agent Run Tracking ─────────────────────────────────────────────


class Conversation(Base):
    """User conversation thread with associated messages."""
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New Conversation")
    messages: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AgentRun(Base):
    """Tracks a single agent execution workflow turn, status, and approval."""
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")  # running, completed, waiting_approval, rejected, failed
    tools_used: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    execution_trace: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_required")  # not_required, pending, approved, rejected
    # The assistant's reply for this turn, so conversation history can be replayed into
    # a later turn's context (resolving follow-ups like "send it to john@example.com").
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── 5. Execution Audit Log ───────────────────────────────────────────────────


class ExecutionLog(Base):
    """Immutable audit record for every tool invocation attempt."""
    __tablename__ = "execution_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default="system")
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # success, error, validation_error
    input_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── 6. LLM Token Usage Tracking ──────────────────────────────────────────────


class TokenUsage(Base):
    """Per-user, per-turn LLM model token usage metadata."""
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="gemini-2.5-flash")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── 7. Human-In-The-Loop Approval Requests ───────────────────────────────────


class ApprovalRequest(Base):
    """Pending or resolved approval requests for high-risk mutating operations."""
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending, approved, rejected
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Backward-compatible alias for previous imports
ToolMetadata = ToolDefinition
ConversationSession = Conversation
