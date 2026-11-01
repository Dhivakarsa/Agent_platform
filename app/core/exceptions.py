"""
Custom exception hierarchy for the agentic system.

All domain-specific exceptions inherit from ``AgenticBaseError`` so that
top-level error handlers can catch the full family in one clause.
"""

from __future__ import annotations

from typing import Any


# =============================================================================
# Base
# =============================================================================

class AgenticBaseError(Exception):
    """Root exception for every error raised by the agentic system."""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, Any] = details or {}


# =============================================================================
# Tool-related errors
# =============================================================================

class ToolNotFoundError(AgenticBaseError):
    """Raised when a requested tool name cannot be resolved."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            f"Tool '{tool_name}' is not registered.",
            details={"tool_name": tool_name},
        )
        self.tool_name = tool_name


class ToolExecutionError(AgenticBaseError):
    """Raised when a tool invocation fails at runtime."""

    def __init__(
        self,
        tool_name: str,
        reason: str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            f"Execution of tool '{tool_name}' failed: {reason}",
            details={"tool_name": tool_name, "reason": reason},
        )
        self.tool_name = tool_name
        self.reason = reason
        self.__cause__ = cause


class ToolValidationError(AgenticBaseError):
    """Raised when tool input arguments fail schema validation."""

    def __init__(self, tool_name: str, validation_errors: list[dict[str, Any]]) -> None:
        super().__init__(
            f"Validation failed for tool '{tool_name}': {validation_errors}",
            details={"tool_name": tool_name, "errors": validation_errors},
        )
        self.tool_name = tool_name
        self.validation_errors = validation_errors


# =============================================================================
# Retrieval / embedding errors
# =============================================================================

class RetrieverError(AgenticBaseError):
    """Raised when the vector-store retriever encounters an error."""

    def __init__(self, message: str = "Retriever operation failed") -> None:
        super().__init__(message)


class EmbeddingError(AgenticBaseError):
    """Raised when embedding generation fails."""

    def __init__(self, message: str = "Embedding generation failed") -> None:
        super().__init__(message)


# =============================================================================
# Agent orchestration errors
# =============================================================================

class AgentError(AgenticBaseError):
    """Generic error within the agent reasoning loop."""

    def __init__(self, message: str = "Agent encountered an error") -> None:
        super().__init__(message)


class MaxIterationsExceeded(AgentError):
    """Raised when the agent exceeds the maximum allowed reasoning iterations."""

    def __init__(self, max_iterations: int) -> None:
        super().__init__(
            f"Agent exceeded the maximum of {max_iterations} iterations.",
        )
        self.max_iterations = max_iterations
        self.details["max_iterations"] = max_iterations
