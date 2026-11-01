"""
Pydantic models for the agent chat API.

These schemas define the contract between the client (Streamlit UI, REST
consumers) and the agent orchestration layer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StreamEventType(StrEnum):
    """Types of server-sent events emitted during streamed agent execution."""

    THINKING = "thinking"
    TOOL_DISCOVERY = "tool_discovery"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINAL = "final"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Payload sent by the client to initiate or continue a conversation.

    Attributes:
        session_id: Optional existing session to continue.  A new session is
            created when ``None``.
        query: The user's natural-language query.
        stream: When ``True`` the server returns an SSE stream of
            ``StreamEvent`` objects instead of a single ``ChatResponse``.
    """

    session_id: str | None = Field(
        default=None,
        description="Existing session UUID.  Omit to start a new conversation.",
        examples=["b4f7c2a1-90d3-4e8a-b5f6-1a2b3c4d5e6f"],
    )
    query: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="User query in natural language.",
        examples=["Create a PayPal order for $49.99"],
    )
    stream: bool = Field(
        default=False,
        description="Enable server-sent event streaming.",
    )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class ChatResponse(BaseModel):
    """Single-shot response returned when streaming is disabled.

    Attributes:
        session_id: The session this response belongs to.
        response: The agent's final natural-language answer.
        tools_used: Ordered list of tool names invoked during this turn.
        execution_trace: Structured trace of every reasoning step for
            observability / debugging.
    """

    session_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Session UUID.",
    )
    response: str = Field(
        ...,
        description="Final agent response in natural language.",
    )
    tools_used: list[str] = Field(
        default_factory=list,
        description="Ordered list of tool names invoked.",
    )
    execution_trace: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Step-by-step execution trace for debugging.",
    )


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class StreamEvent(BaseModel):
    """A single event in the server-sent event stream.

    Attributes:
        event_type: Discriminator indicating the event category.
        data: Payload whose shape depends on ``event_type``.
    """

    event_type: StreamEventType = Field(
        ...,
        description="Category of the stream event.",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific payload.",
    )
