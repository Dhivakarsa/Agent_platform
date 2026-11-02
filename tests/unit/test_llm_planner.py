"""
Unit tests for the Gemini reasoning layer and its wiring into the graph.

The Gemini client is stubbed, so these run with no API key and no network: what is under
test is our own contract translation, shortlist enforcement, token accounting and
fallback behaviour, not the model itself.
"""

from __future__ import annotations

import pytest

from app.graph.nodes import plan_and_select_node
from app.schemas.tool import ParameterSchema, RegisteredTool
from app.services.llm_service import LLMService, TokenUsage


# ── Stub SDK surface ─────────────────────────────────────────────────────────


class _StubUsage:
    def __init__(self, prompt: int, candidates: int) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates


class _StubFunctionCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class _StubPart:
    def __init__(self, function_call=None, text=None) -> None:
        self.function_call = function_call
        self.text = text


class _StubResponse:
    def __init__(self, parts, prompt_tokens=120, output_tokens=18) -> None:
        content = type("C", (), {"parts": parts})()
        self.candidates = [type("Cand", (), {"content": content})()]
        self.usage_metadata = _StubUsage(prompt_tokens, output_tokens)
        self.text = None


class _StubModels:
    def __init__(self, response) -> None:
        self._response = response
        self.last_config = None

    async def generate_content(self, model, contents, config):
        self.last_config = config
        self.last_contents = contents
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _StubClient:
    def __init__(self, response) -> None:
        self.models = _StubModels(response)
        self.aio = type("Aio", (), {"models": self.models})()


def _service_with(response) -> LLMService:
    """An LLMService wired to a stub client but the real google.genai type system."""
    from google.genai import types

    svc = LLMService()
    svc._client = _StubClient(response)
    svc._types = types
    return svc


CANDIDATES = [
    {
        "name": "paypal_send_invoice",
        "description": "Send a draft invoice to its recipient",
        "risk_level": "external_write",
        "parameters": [
            {"name": "invoice_id", "type": "string", "required": True, "location": "path"},
            {"name": "recipient_email", "type": "string", "required": False, "location": "body"},
        ],
    },
    {
        "name": "paypal_list_invoices",
        "description": "List invoices",
        "risk_level": "read_only",
        "parameters": [{"name": "page_size", "type": "number", "required": False, "location": "query"}],
    },
]


# ── Schema translation ───────────────────────────────────────────────────────


def test_function_declarations_carry_types_and_required_fields():
    svc = _service_with(None)
    decls = svc._build_function_declarations(CANDIDATES)

    assert [d.name for d in decls] == ["paypal_send_invoice", "paypal_list_invoices"]

    send = decls[0]
    assert send.parameters.required == ["invoice_id"]
    assert send.parameters.properties["invoice_id"].type == "STRING"
    # Risk is surfaced to the model so it can prefer read-only tools.
    assert "external_write" in send.description

    assert decls[1].parameters.properties["page_size"].type == "NUMBER"


def test_array_parameters_declare_an_item_type():
    svc = _service_with(None)
    decls = svc._build_function_declarations(
        [{"name": "t", "description": "d", "parameters": [{"name": "ids", "type": "array", "required": True}]}]
    )
    # Gemini rejects an ARRAY schema with no items type.
    assert decls[0].parameters.properties["ids"].items is not None


def test_tool_with_no_parameters_declares_no_schema():
    svc = _service_with(None)
    decls = svc._build_function_declarations([{"name": "ping", "description": "d", "parameters": []}])
    assert decls[0].parameters is None


# ── Tool choice ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_choose_tool_returns_call_with_real_token_usage():
    response = _StubResponse(
        [_StubPart(function_call=_StubFunctionCall("paypal_send_invoice", {"invoice_id": "INV2-9"}))],
        prompt_tokens=120,
        output_tokens=18,
    )
    choice = await _service_with(response).choose_tool("send invoice INV2-9", CANDIDATES)

    assert choice is not None and choice.is_tool_call
    assert choice.tool_name == "paypal_send_invoice"
    assert choice.arguments == {"invoice_id": "INV2-9"}
    # Token counts come from the SDK, not from a word count.
    assert choice.usage.input_tokens == 120
    assert choice.usage.output_tokens == 18
    assert choice.usage.total_tokens == 138


@pytest.mark.asyncio
async def test_model_cannot_escape_the_candidate_shortlist():
    """A hallucinated tool name must never reach the execution engine."""
    response = _StubResponse([_StubPart(function_call=_StubFunctionCall("paypal_delete_everything", {}))])
    choice = await _service_with(response).choose_tool("do it", CANDIDATES)

    assert choice is not None
    assert not choice.is_tool_call
    assert choice.tool_name is None


@pytest.mark.asyncio
async def test_text_reply_is_returned_as_a_direct_answer():
    response = _StubResponse([_StubPart(text="Which invoice should I send?")])
    choice = await _service_with(response).choose_tool("send an invoice", CANDIDATES)

    assert choice is not None
    assert not choice.is_tool_call
    assert choice.direct_answer == "Which invoice should I send?"


@pytest.mark.asyncio
async def test_completed_steps_are_given_to_the_planner():
    """Multi-step chaining depends on prior results reaching the prompt."""
    response = _StubResponse([_StubPart(text="done")])
    svc = _service_with(response)
    await svc.choose_tool(
        "email me the report",
        CANDIDATES,
        completed_steps=[{"tool": "paypal_list_transactions", "result": {"total_items": 7}}],
    )
    assert "paypal_list_transactions" in svc._client.models.last_contents
    assert "total_items" in svc._client.models.last_contents


@pytest.mark.asyncio
async def test_model_failure_falls_back_rather_than_raising():
    choice = await _service_with(RuntimeError("503 unavailable")).choose_tool("x", CANDIDATES)
    assert choice is None  # None signals the caller to use the deterministic planner


@pytest.mark.asyncio
async def test_unconfigured_service_is_unavailable(monkeypatch):
    svc = LLMService()
    # Patch the instance's view of settings; the real object is a process-wide singleton.
    monkeypatch.setattr(svc, "settings", type("S", (), {"GEMINI_API_KEY": "", "gemini_model": "m"})())
    assert await svc.choose_tool("x", CANDIDATES) is None
    assert not svc.available
    assert "not configured" in (svc.unavailable_reason or "")


# ── Graph wiring ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_node_uses_model_choice_and_accumulates_tokens(monkeypatch):
    response = _StubResponse(
        [_StubPart(function_call=_StubFunctionCall("paypal_list_invoices", {"page_size": 5}))],
        prompt_tokens=90,
        output_tokens=10,
    )
    svc = _service_with(response)
    monkeypatch.setattr("app.graph.nodes.get_llm_service", lambda: svc)

    state = {
        "user_prompt": "show me my invoices",
        "candidate_summaries": CANDIDATES,
        "token_usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
        "messages": [],
        "execution_trace": [],
    }
    result = await plan_and_select_node(state)

    assert result["pending_tool_call"] == {
        "name": "paypal_list_invoices",
        "arguments": {"page_size": 5},
    }
    # Usage accumulates on top of what earlier nodes already recorded.
    assert result["token_usage"] == {"input_tokens": 95, "output_tokens": 15, "total_tokens": 110}
    stage = result["timeline_events"][-1]
    assert stage["metadata"]["planner"] == "gemini_function_calling"


@pytest.mark.asyncio
async def test_planner_node_falls_back_to_rules_when_model_is_down(monkeypatch):
    svc = _service_with(RuntimeError("boom"))
    monkeypatch.setattr("app.graph.nodes.get_llm_service", lambda: svc)

    state = {
        "user_prompt": "what is 2 + 2",
        "intent": "CALCULATION",
        "candidate_summaries": [{"name": "calculator_evaluate", "parameters": []}],
        "candidate_tools": [{"name": "calculator_evaluate"}],
        "dependency_mapping": {},
        "messages": [],
        "execution_trace": [],
    }
    result = await plan_and_select_node(state)

    assert result["pending_tool_call"]["name"] == "calculator_evaluate"
    assert result["timeline_events"][-1]["metadata"]["planner"] == "deterministic"


@pytest.mark.asyncio
async def test_clarification_short_circuits_planning(monkeypatch):
    svc = _service_with(_StubResponse([_StubPart(text="should not be called")]))
    monkeypatch.setattr("app.graph.nodes.get_llm_service", lambda: svc)

    result = await plan_and_select_node({"requires_clarification": True, "user_prompt": "send an email"})

    assert result["pending_tool_call"] is None
    assert svc._client.models.last_config is None  # the model was never consulted


# ── Token arithmetic ─────────────────────────────────────────────────────────


def test_token_usage_adds_across_calls():
    total = TokenUsage(10, 2) + TokenUsage(5, 3)
    assert total.as_dict() == {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20}
