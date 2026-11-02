"""
Tests for observability configuration and the browser origin policy.

Tracing must be optional: with no key the platform runs unchanged and ``traced`` adds no
behaviour. CORS must not combine a wildcard origin with credentials, which would let any
site call the API using a signed-in user's token.
"""

from __future__ import annotations

import pytest

import app.core.tracing as tracing_module
from app.config import Settings, get_settings
from app.core.tracing import configure_tracing, traced


@pytest.fixture(autouse=True)
def reset_tracing_state(monkeypatch):
    """
    Isolate each test from the cached tracing configuration.

    Also pins the LangSmith background uploader off for the whole module. Without this,
    a test that enables tracing causes the client to attempt real HTTP submissions to the
    configured endpoint, so the suite would do network I/O and emit connection errors.
    """
    tracing_module._configured = None
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    yield
    tracing_module._configured = None


@pytest.fixture
def no_upload(monkeypatch):
    """Force our tracing checks on while keeping the LangSmith client inert."""
    monkeypatch.setattr(tracing_module, "tracing_enabled", lambda: True)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


# ── Tracing ──────────────────────────────────────────────────────────────────


def test_tracing_is_off_without_a_key(monkeypatch):
    monkeypatch.setattr(
        tracing_module, "get_settings",
        lambda: type("S", (), {
            "langsmith_tracing": True, "langsmith_api_key": "",
            "langsmith_project": "p", "langsmith_endpoint": "e",
        })(),
    )
    assert configure_tracing() is False


def test_tracing_is_off_when_explicitly_disabled(monkeypatch):
    monkeypatch.setattr(
        tracing_module, "get_settings",
        lambda: type("S", (), {
            "langsmith_tracing": False, "langsmith_api_key": "a-key",
            "langsmith_project": "p", "langsmith_endpoint": "e",
        })(),
    )
    assert configure_tracing() is False


def test_enabling_tracing_sets_both_variable_generations(monkeypatch):
    """Which env var applies depends on library versions, so both are set."""
    for name in (
        "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING",
        "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY",
        "LANGCHAIN_PROJECT", "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        tracing_module, "get_settings",
        lambda: type("S", (), {
            "langsmith_tracing": True, "langsmith_api_key": "test-key",
            "langsmith_project": "proj", "langsmith_endpoint": "https://api.smith.langchain.com",
        })(),
    )

    assert configure_tracing() is True
    import os

    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGCHAIN_API_KEY"] == "test-key"
    assert os.environ["LANGSMITH_API_KEY"] == "test-key"
    assert os.environ["LANGCHAIN_PROJECT"] == "proj"
    assert os.environ["LANGSMITH_PROJECT"] == "proj"


@pytest.mark.asyncio
async def test_traced_is_transparent_when_disabled(monkeypatch):
    """With tracing off the decorator must return the function untouched."""
    monkeypatch.setattr(tracing_module, "tracing_enabled", lambda: False)

    async def original(x):
        return x * 2

    wrapped = traced("name")(original)
    assert wrapped is original
    assert await wrapped(21) == 42


@pytest.mark.asyncio
async def test_traced_preserves_behaviour_when_enabled(no_upload):
    """Wrapping must not change what the function returns or raises."""

    async def returns_value(x):
        return {"doubled": x * 2}

    async def raises():
        raise ValueError("propagated")

    assert await traced("ok")(returns_value)(5) == {"doubled": 10}

    with pytest.raises(ValueError, match="propagated"):
        await traced("boom")(raises)()


@pytest.mark.asyncio
async def test_reasoning_still_works_with_tracing_enabled(no_upload, monkeypatch):
    """
    The planner is wrapped for tracing, so confirm the wrapper does not change its
    contract when tracing is on.
    """
    from app.services.llm_service import LLMService

    svc = LLMService()
    # No client configured, so the planner must still report "unavailable" as None.
    monkeypatch.setattr(svc, "_ensure_client", lambda: None)
    assert await svc.choose_tool("anything", [{"name": "t", "parameters": []}]) is None
    assert await svc.synthesize_answer("anything", []) is None


# ── CORS ─────────────────────────────────────────────────────────────────────


def test_cors_origins_are_explicit_not_wildcard():
    origins = get_settings().cors_allow_origins_list
    assert origins, "no CORS origins configured"
    assert "*" not in origins, "a wildcard origin with credentials exposes user tokens"
    assert any("8501" in o for o in origins), "the Streamlit frontend is not allowed"


def test_cors_origins_parse_from_a_comma_separated_value():
    s = Settings(cors_allow_origins="http://a.test, http://b.test ,, http://c.test")
    assert s.cors_allow_origins_list == ["http://a.test", "http://b.test", "http://c.test"]


def test_app_middleware_does_not_pair_wildcard_with_credentials():
    from app.main import app

    for middleware in app.user_middleware:
        options = getattr(middleware, "kwargs", {}) or {}
        if "allow_origins" not in options:
            continue
        if options.get("allow_credentials"):
            assert "*" not in options["allow_origins"]
