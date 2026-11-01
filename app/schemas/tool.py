"""
Pydantic models for tool definitions, registration, admin creation, and execution results.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────


class HttpMethod(str, Enum):
    """Supported HTTP methods for API tools."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ToolStatus(str, Enum):
    """Lifecycle status of a registered tool."""
    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class ToolSourceType(str, Enum):
    """Origin source of the tool implementation."""
    HTTP_API = "http_api"
    MCP = "mcp"
    INTERNAL = "internal"


class ToolDomain(str, Enum):
    """Broad operational domains for tool classification."""
    INVOICING = "invoicing"
    PAYMENTS = "payments"
    ORDERS = "orders"
    DISPUTES = "disputes"
    REPORTING = "reporting"
    SUBSCRIPTIONS = "subscriptions"
    PAYOUTS = "payouts"
    WEBHOOKS = "webhooks"
    IDENTITY = "identity"
    CATALOG = "catalog"
    MESSAGING = "messaging"
    PROJECT_MANAGEMENT = "project_management"
    VERSION_CONTROL = "version_control"
    CRM = "crm"
    CLOUD_INFRA = "cloud_infra"
    GENERAL = "general"


class ExecutionStatus(str, Enum):
    """Outcome status of a tool execution."""
    SUCCESS = "success"
    VALIDATION_ERROR = "validation_error"
    HTTP_ERROR = "http_error"
    WAITING_APPROVAL = "waiting_approval"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    AUTH_ERROR = "auth_error"
    INTERNAL_ERROR = "internal_error"


class RiskLevel(str, Enum):
    """Granular risk tiers defined in Section 8 of the Implementation Plan."""
    READ_ONLY = "read_only"
    USER_SCOPED_READ = "user_scoped_read"
    REVERSIBLE_WRITE = "reversible_write"
    EXTERNAL_WRITE = "external_write"
    FINANCIAL = "financial"
    DESTRUCTIVE = "destructive"


class ApprovalPolicy(str, Enum):
    """Approval policy contract defined in Section 8 of the Implementation Plan."""
    AUTO_APPROVED = "auto_approved"
    REQUIRE_APPROVAL = "require_approval"
    ADMIN_ONLY = "admin_only"


# ── Parameter & Dependency Schemas ──────────────────────────────────────────


class ParameterSchema(BaseModel):
    """Parameter definition for path, query, header, or body."""
    name: str = Field(..., description="Parameter name")
    type: str = Field(default="string", description="Parameter type: string, number, integer, boolean, object, array")
    description: str = Field(default="", description="Human-readable description")
    required: bool = Field(default=False, description="Whether parameter is required")
    default: Any = Field(default=None, description="Default value if not supplied")
    location: Literal["path", "query", "header", "body"] = "body"


ParameterDefinition = ParameterSchema


class ToolDependency(BaseModel):
    """Prerequisite tool execution mapping."""
    tool: str = Field(..., description="Name of the required prior tool, e.g. 'paypal_create_draft_invoice'")
    output_field: str = Field(default="id", description="Field name in prior tool's output, e.g. 'id'")
    input_field: str = Field(default="invoice_id", description="Field name to populate in this tool, e.g. 'invoice_id'")


# ── Registered Tool (Canonical Representation) ──────────────────────────────


class RegisteredTool(BaseModel):
    """Canonical tool representation shared across storage, discovery, and execution."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_id: Optional[str] = None
    name: str = Field(..., description="Unique snake_case identifier, e.g. 'paypal_create_draft_invoice'")
    display_name: str = Field(default="")
    namespace: str = Field(default="paypal", description="Hierarchical registry namespace e.g. 'paypal.invoicing', 'filesystem', 'communication.email'")
    capabilities: list[str] = Field(default_factory=list, description="Machine-readable capabilities e.g. ['email.send', 'external_write']")
    domain: str = Field(default="general", description="Operational category, e.g. 'invoicing', 'payments'")
    category: Optional[str] = None
    service: str = Field(default="PayPal")
    method: str = Field(default="GET")
    path: str = Field(default="", description="API endpoint path")
    endpoint_path: Optional[str] = Field(default="", description="Alias for path")
    description: str = Field(default="")
    version: str = Field(default="1.0.0")
    status: str = Field(default="enabled")

    # API technical config
    api_type: str = Field(default="REST")
    base_url: str = Field(default="https://api-m.sandbox.paypal.com")
    content_type: str = Field(default="application/json")
    timeout_seconds: int = Field(default=30)
    auth_profile: str = Field(default="PAYPAL_SANDBOX")

    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    parameters: list[ParameterSchema] = Field(default_factory=list)
    path_params: list[ParameterSchema] = Field(default_factory=list)
    query_params: list[ParameterSchema] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    request_body_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)

    # Governance & Risk
    is_mutating: bool = Field(default=False)
    approval_required: bool = Field(default=False)
    risk_level: RiskLevel = Field(default=RiskLevel.READ_ONLY, description="Operational risk tier")
    approval_policy: str = Field(default="standard", description="Policy rule key: standard, always_confirm_payload_hash, none")
    permitted_roles: list[str] = Field(default_factory=lambda: ["admin", "user"])
    idempotency_supported: bool = Field(default=True)
    idempotency: str = Field(default="standard", description="Idempotency policy: none, standard, provider_reconciliation_required")
    retry_config: dict[str, Any] = Field(default_factory=lambda: {"max_retries": 2, "backoff": 1.0})

    requires_prior: list[str] = Field(default_factory=list)
    dependencies: list[ToolDependency] = Field(default_factory=list)
    source: str = Field(default="registry")
    source_type: ToolSourceType = Field(default=ToolSourceType.HTTP_API)
    mcp_server_name: Optional[str] = Field(default=None, description="Associated MCP server name if source_type is mcp")
    remote_tool_name: Optional[str] = Field(default=None, description="Actual name exposed by the MCP server")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"from_attributes": True}


# ── Admin Tool Management Requests ──────────────────────────────────────────


class ToolCreateRequest(BaseModel):
    """Full 10-section tool creation payload from the Admin Screen."""
    # Section 1: Basic Information
    name: str = Field(..., min_length=2, max_length=128, description="Unique snake_case identifier")
    display_name: str = Field(..., min_length=2, max_length=128)
    description: str = Field(..., min_length=5)
    category: str = Field(..., examples=["invoicing", "payments", "disputes", "reporting"])
    service: str = Field(default="PayPal")
    tags: list[str] = Field(default_factory=list)
    version: str = Field(default="1.0.0")
    status: ToolStatus = Field(default=ToolStatus.ENABLED)

    # Section 2: API Configuration
    api_type: str = Field(default="REST")
    method: HttpMethod = Field(default=HttpMethod.POST)
    base_url: str = Field(default="https://api-m.sandbox.paypal.com")
    endpoint_path: str = Field(..., examples=["/v2/invoicing/invoices"])
    content_type: str = Field(default="application/json")
    timeout_seconds: int = Field(default=30)
    auth_profile: str = Field(default="PAYPAL_SANDBOX")

    # Sections 3-5: Parameters and Headers
    path_params: list[ParameterSchema] = Field(default_factory=list)
    query_params: list[ParameterSchema] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)

    # Sections 6-7: Schemas
    request_body_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)

    # Section 8: Execution Behavior
    is_mutating: bool = Field(default=False)
    approval_required: bool = Field(default=False)
    permitted_roles: list[str] = Field(default_factory=lambda: ["admin", "user"])
    idempotency_supported: bool = Field(default=True)
    retry_config: dict[str, Any] = Field(default_factory=lambda: {"max_retries": 2, "backoff": 1.0})

    # Section 9: Dependencies
    dependencies: list[ToolDependency] = Field(default_factory=list)


class ToolUpdateRequest(BaseModel):
    """Partial update payload for editing a registered tool."""
    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    status: Optional[ToolStatus] = None
    timeout_seconds: Optional[int] = None
    is_mutating: Optional[bool] = None
    approval_required: Optional[bool] = None
    permitted_roles: Optional[list[str]] = None
    request_body_schema: Optional[dict[str, Any]] = None
    response_schema: Optional[dict[str, Any]] = None
    dependencies: Optional[list[ToolDependency]] = None


class ToolStatusPatchRequest(BaseModel):
    """Quick enable/disable/archive status toggle."""
    status: ToolStatus


class ToolValidateRequest(BaseModel):
    """Schema validation request before saving."""
    schema_type: Literal["request", "response"] = "request"
    schema_dict: dict[str, Any]
    sample_payload: Optional[dict[str, Any]] = None


class ToolTestRequest(BaseModel):
    """Run mock/sandbox test against configured tool."""
    test_mode: Literal["mock", "sandbox"] = "mock"
    input_arguments: dict[str, Any] = Field(default_factory=dict)


# ── Tool Call Outcome ────────────────────────────────────────────────────────


class ToolCallResult(BaseModel):
    """Structured outcome of executing an individual tool."""
    tool_name: str
    session_id: str
    status: ExecutionStatus
    http_status_code: Optional[int] = None
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input_arguments: dict[str, Any] = Field(default_factory=dict)
    response_payload: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"from_attributes": True}


class ToolCategory(BaseModel):
    """Lightweight category grouping for browsing."""
    domain: str
    display_name: str
    description: str
    tool_count: int = 0
