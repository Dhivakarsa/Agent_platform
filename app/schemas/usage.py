"""
Pydantic schemas for personal usage, execution history, and admin analytics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class ExecutionRecordResponse(BaseModel):
    """User-level task execution summary record."""
    request_id: str
    query: str
    status: str
    tools_used: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: float = 0.0
    created_at: datetime
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TokenRecordResponse(BaseModel):
    """Token consumption record per model invocation."""
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUsageSummary(BaseModel):
    """Aggregated usage and token metrics for a single user."""
    user_id: str
    email: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    recent_executions: list[ExecutionRecordResponse] = Field(default_factory=list)


class AdminUserSummary(BaseModel):
    """User row summary in the Administrator Management dashboard."""
    user_id: str
    email: str
    role: str
    status: str
    total_tasks: int
    total_tokens: int
    last_active: Optional[datetime] = None
    created_at: datetime


class AdminUsageOverallSummary(BaseModel):
    """System-wide summary metrics for administrators."""
    total_users: int
    total_executions: int
    total_tokens_consumed: int
    active_tools_count: int
    indexed_documents_count: int

