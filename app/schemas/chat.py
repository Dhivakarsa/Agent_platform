"""
Pydantic schemas for chat interactions, agent runs, and approval actions.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Payload sent by the client to execute a chat prompt."""
    query: str = Field(..., min_length=1, max_length=4096, description="User instruction")
    conversation_id: Optional[str] = Field(default=None, description="Thread conversation UUID")
    stream: bool = Field(default=False, description="Enable SSE event streaming")


class ApprovalActionRequest(BaseModel):
    """Payload to approve or reject a pending mutating action."""
    action: Literal["approve", "reject"] = Field(..., description="Decision")
    reason: Optional[str] = Field(default=None, description="Optional explanation")


class ChatResponse(BaseModel):
    """Response returned upon completing or pausing an agent workflow."""
    conversation_id: str
    run_id: str
    response: str
    status: str = "completed"  # completed, waiting_approval, rejected, failed
    approval_status: str = "not_required"
    pending_approval: Optional[dict[str, Any]] = None
    tools_used: list[str] = Field(default_factory=list)
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    timeline_events: list[dict[str, Any]] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

