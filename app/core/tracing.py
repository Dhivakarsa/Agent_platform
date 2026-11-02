"""
LangSmith tracing.

LangGraph emits traces to LangSmith when the LangChain tracing environment variables are
set, which gives a span per pipeline node for free. The reasoning calls go directly to
google-genai rather than through LangChain, so they are traced explicitly here - which is
where the prompt, the candidate shortlist, the chosen tool and the token counts live.

Tracing is off unless a key is configured, and ``traced`` degrades to a no-op decorator so
nothing in the call path depends on LangSmith being installed or reachable.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Optional, TypeVar

from app.config import get_settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_configured: Optional[bool] = None


def configure_tracing() -> bool:
    """
    Enable LangSmith tracing if it is configured, and report whether it is on.

    Sets both the legacy LANGCHAIN_* and current LANGSMITH_* variable names, since which
    one applies depends on the installed library versions.
    """
    global _configured
    if _configured is not None:
        return _configured

    settings = get_settings()
    api_key = settings.langsmith_api_key

    if not (settings.langsmith_tracing and api_key):
        _configured = False
        logger.info(
            "LangSmith tracing disabled (set LANGSMITH_TRACING=true and LANGSMITH_API_KEY "
            "to enable). Per-turn timeline and audit tables remain available."
        )
        return False

    try:
        import langsmith  # noqa: F401
    except Exception as exc:
        _configured = False
        logger.warning("LangSmith requested but the package is unavailable (%s).", exc)
        return False

    for name, value in (
        ("LANGCHAIN_TRACING_V2", "true"),
        ("LANGSMITH_TRACING", "true"),
        ("LANGCHAIN_API_KEY", api_key),
        ("LANGSMITH_API_KEY", api_key),
        ("LANGCHAIN_PROJECT", settings.langsmith_project),
        ("LANGSMITH_PROJECT", settings.langsmith_project),
        ("LANGCHAIN_ENDPOINT", settings.langsmith_endpoint),
        ("LANGSMITH_ENDPOINT", settings.langsmith_endpoint),
    ):
        os.environ[name] = value

    _configured = True
    logger.info(
        "LangSmith tracing enabled (project=%s, endpoint=%s)",
        settings.langsmith_project,
        settings.langsmith_endpoint,
    )
    return True


def tracing_enabled() -> bool:
    """Whether tracing is currently active."""
    return bool(_configured) if _configured is not None else configure_tracing()


def traced(name: str, run_type: str = "chain") -> Callable[[F], F]:
    """
    Decorate a function so its inputs, outputs and errors become a LangSmith span.

    When tracing is not configured the function is returned unchanged, so there is no
    import cost and no wrapper overhead on the hot path.
    """

    def decorator(func: F) -> F:
        if not tracing_enabled():
            return func
        try:
            from langsmith import traceable

            return traceable(name=name, run_type=run_type)(func)
        except Exception as exc:
            logger.debug("Could not wrap %s for tracing: %s", name, exc)
            return func

    return decorator
