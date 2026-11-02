"""
Gemini reasoning service: function calling, grounded synthesis, and real token accounting.

This is the reasoning layer the agent graph binds to. It converts bounded candidate tool
schemas into Gemini function declarations, asks the model to choose exactly one tool and
populate its arguments, and reports the token usage the SDK actually returned.

When no API key is configured the service degrades to ``available = False`` and callers
fall back to their deterministic paths, which keeps the platform runnable offline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.core.tracing import traced

logger = logging.getLogger(__name__)

# Gemini accepts a restricted subset of JSON Schema for function declarations.
_JSON_TYPE_MAP = {
    "string": "STRING",
    "str": "STRING",
    "integer": "INTEGER",
    "int": "INTEGER",
    "number": "NUMBER",
    "float": "NUMBER",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "array": "ARRAY",
    "list": "ARRAY",
    "object": "OBJECT",
    "dict": "OBJECT",
}


@dataclass
class TokenUsage:
    """Token counts reported by the Gemini SDK for a single generate_content call."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ToolChoice:
    """A model-selected tool call, or a direct answer when no tool is appropriate."""

    tool_name: Optional[str] = None
    arguments: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    direct_answer: Optional[str] = None
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def is_tool_call(self) -> bool:
        return bool(self.tool_name)


PLANNER_SYSTEM_PROMPT = """You are the planning component of an API orchestration agent.

You are given a user request and a SHORTLIST of candidate tools that a retrieval stage has \
already narrowed down from a catalog of hundreds of APIs. Your job is to pick the single best \
NEXT tool to call and populate its arguments from the user's request.

Rules you must follow:
1. Call at most ONE tool. The orchestrator will call you again for subsequent steps.
2. You may ONLY call a tool from the provided shortlist. Never invent a tool name.
3. Populate arguments ONLY from information the user actually supplied, or from the results of \
previous steps shown to you. Never invent identifiers, email addresses, amounts or dates.
4. If a required argument is genuinely missing and cannot be inferred, do NOT call the tool. \
Reply with plain text asking the user for precisely the missing piece of information.
5. If the request needs no tool at all (a greeting, or a question you can answer from the \
conversation), reply with plain text instead of calling a tool.
6. Prefer a read-only tool over a mutating one when both would satisfy the request."""


SYNTHESIS_SYSTEM_PROMPT = """You are the response component of an API orchestration agent.

You are given a user request and the raw JSON results of the tool calls that were executed to \
satisfy it. Write the reply the user should see.

Rules you must follow:
1. Ground every factual claim in the tool results provided. Never invent numbers, IDs, dates or \
statuses that are not present in the data.
2. If the results are documentation excerpts, answer the user's question from them and cite the \
document titles you used.
3. Be concise. Lead with the answer. Use short Markdown (bold for key values, bullets for lists).
4. Surface identifiers the user will need next, such as an invoice ID.
5. If the results contain an error, explain what failed in plain language and what the user can do.
6. Do not describe your own internal steps, tool names, or architecture."""


class LLMService:
    """Wraps Gemini for tool-choice planning and grounded answer synthesis."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.model = self.settings.gemini_model
        self._client: Any = None
        self._types: Any = None
        self._unavailable_reason: Optional[str] = None

    # ── Client lifecycle ─────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when a Gemini client could be constructed, so real reasoning is possible."""
        return self._ensure_client() is not None

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._unavailable_reason

    def _ensure_client(self) -> Any:
        """Lazily construct the GenAI client. Returns None when unavailable."""
        if self._client is not None:
            return self._client
        if self._unavailable_reason is not None:
            return None

        api_key = self.settings.GEMINI_API_KEY
        if not api_key:
            self._unavailable_reason = "GEMINI_API_KEY is not configured"
            logger.info("Gemini reasoning disabled: %s. Falling back to deterministic planner.", self._unavailable_reason)
            return None

        try:
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=api_key)
            self._types = types
            logger.info("Gemini reasoning enabled (model=%s)", self.model)
            return self._client
        except Exception as exc:
            self._unavailable_reason = f"GenAI client unavailable: {exc}"
            logger.warning("Gemini reasoning disabled: %s", self._unavailable_reason)
            return None

    # ── Schema translation ───────────────────────────────────────────────

    def _parameter_properties(self, parameters: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        """Translate stored ParameterSchema dicts into a Gemini OBJECT schema."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param in parameters or []:
            name = param.get("name")
            if not name:
                continue

            raw_type = str(param.get("type", "string")).lower()
            schema: dict[str, Any] = {"type": _JSON_TYPE_MAP.get(raw_type, "STRING")}

            description = param.get("description") or f"The {name.replace('_', ' ')}"
            if param.get("enum"):
                description = f"{description}. Allowed values: {', '.join(str(e) for e in param['enum'])}"
            schema["description"] = description

            # Gemini requires ARRAY schemas to declare their item type.
            if schema["type"] == "ARRAY":
                schema["items"] = {"type": "STRING"}

            properties[name] = schema
            if param.get("required"):
                required.append(name)

        return properties, required

    def _build_function_declarations(self, candidates: list[dict[str, Any]]) -> list[Any]:
        """Build one Gemini FunctionDeclaration per candidate tool."""
        types = self._types
        declarations = []

        for tool in candidates:
            name = tool.get("name")
            if not name:
                continue

            properties, required = self._parameter_properties(tool.get("parameters", []))

            # Describe risk inline so the model prefers read-only tools when both would do.
            description = tool.get("description") or name
            risk = tool.get("risk_level")
            if risk and str(risk) not in ("read_only", "RiskLevel.READ_ONLY"):
                description = f"{description} (Risk: {risk})"

            parameters_schema = None
            if properties:
                parameters_schema = types.Schema(
                    type="OBJECT",
                    properties={k: types.Schema(**v) for k, v in properties.items()},
                    required=required or None,
                )

            declarations.append(
                types.FunctionDeclaration(
                    name=name,
                    description=description[:1024],
                    parameters=parameters_schema,
                )
            )

        return declarations

    # ── Token extraction ─────────────────────────────────────────────────

    @staticmethod
    def _extract_usage(response: Any) -> TokenUsage:
        """Read the real token counts the SDK reported for this call."""
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return TokenUsage()
        return TokenUsage(
            input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0),
        )

    @staticmethod
    def _extract_function_call(response: Any) -> Optional[Any]:
        """Return the first function call in the response, if the model made one."""
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                call = getattr(part, "function_call", None)
                if call is not None and getattr(call, "name", None):
                    return call
        return None

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Concatenate the text parts of a response, tolerating SDK shape differences."""
        direct = getattr(response, "text", None)
        if direct:
            return str(direct).strip()

        chunks: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks).strip()

    # ── Planning ─────────────────────────────────────────────────────────

    async def choose_tool(
        self,
        user_prompt: str,
        candidates: list[dict[str, Any]],
        history: Optional[list[dict[str, Any]]] = None,
        completed_steps: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[ToolChoice]:
        """
        Ask Gemini to select the next tool from ``candidates`` and populate its arguments.

        Returns None when the model is unavailable or the call fails, so the caller can
        fall back to its deterministic planner. A successful call returns a ToolChoice that
        either carries a tool call or a direct textual answer.
        """

        @traced("plan_and_select.gemini", run_type="llm")
        async def _plan(prompt: str, shortlist: list[str]) -> Optional[ToolChoice]:
            # shortlist is passed so the candidate set is visible in the trace.
            return await self._choose_tool_impl(user_prompt, candidates, history, completed_steps)

        return await _plan(user_prompt, [c.get("name", "") for c in candidates])

    async def _choose_tool_impl(
        self,
        user_prompt: str,
        candidates: list[dict[str, Any]],
        history: Optional[list[dict[str, Any]]] = None,
        completed_steps: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[ToolChoice]:
        """Build the function declarations, call the model, and interpret the response."""
        client = self._ensure_client()
        if client is None or not candidates:
            return None

        types = self._types

        try:
            declarations = self._build_function_declarations(candidates)
            if not declarations:
                return None

            parts: list[str] = []

            if history:
                turns = [
                    f"{m.get('role', 'user')}: {m.get('content', '')}"
                    for m in history[-6:]
                    if m.get("content")
                ]
                if turns:
                    parts.append("Conversation so far:\n" + "\n".join(turns))

            if completed_steps:
                parts.append(
                    "Results of steps already executed for this request:\n"
                    + json.dumps(completed_steps, indent=2, default=str)[:4000]
                )

            parts.append(f"User request: {user_prompt}")
            parts.append(
                "Select the single best next tool from the provided shortlist and fill in its "
                "arguments, or reply with plain text if a tool cannot or should not be called."
            )

            response = await client.aio.models.generate_content(
                model=self.model,
                contents="\n\n".join(parts),
                config=types.GenerateContentConfig(
                    system_instruction=PLANNER_SYSTEM_PROMPT,
                    tools=[types.Tool(function_declarations=declarations)],
                    temperature=0.0,
                ),
            )

            usage = self._extract_usage(response)
            call = self._extract_function_call(response)

            if call is not None:
                raw_args = dict(getattr(call, "args", None) or {})
                # Never let the model escape the shortlist.
                allowed = {t.get("name") for t in candidates}
                if call.name not in allowed:
                    logger.warning("Model proposed out-of-shortlist tool %r; ignoring.", call.name)
                    return ToolChoice(
                        direct_answer=None,
                        reasoning=f"Model proposed unavailable tool {call.name}",
                        usage=usage,
                    )
                return ToolChoice(
                    tool_name=call.name,
                    arguments=raw_args,
                    reasoning=f"Gemini selected {call.name} from {len(candidates)} candidates",
                    usage=usage,
                )

            text = self._extract_text(response)
            return ToolChoice(
                direct_answer=text or None,
                reasoning="Model answered directly without a tool call",
                usage=usage,
            )

        except Exception as exc:
            logger.warning("Gemini planning call failed (%s); falling back to deterministic planner.", exc)
            return None

    # ── Synthesis ────────────────────────────────────────────────────────

    async def synthesize_answer(
        self,
        user_prompt: str,
        execution_results: list[dict[str, Any]],
        history: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[tuple[str, TokenUsage]]:
        """
        Write the user-facing answer grounded in ``execution_results``.

        Returns (text, usage), or None when unavailable so the caller can fall back to
        its template renderer.
        """

        @traced("compose_final_answer.gemini", run_type="llm")
        async def _synthesize(prompt: str, step_count: int) -> Optional[tuple[str, TokenUsage]]:
            return await self._synthesize_answer_impl(user_prompt, execution_results, history)

        return await _synthesize(user_prompt, len(execution_results))

    async def _synthesize_answer_impl(
        self,
        user_prompt: str,
        execution_results: list[dict[str, Any]],
        history: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[tuple[str, TokenUsage]]:
        """Call the model to write the grounded reply."""
        client = self._ensure_client()
        if client is None:
            return None

        types = self._types

        try:
            parts: list[str] = []

            if history:
                turns = [
                    f"{m.get('role', 'user')}: {m.get('content', '')}"
                    for m in history[-4:]
                    if m.get("content")
                ]
                if turns:
                    parts.append("Conversation so far:\n" + "\n".join(turns))

            parts.append(f"User request: {user_prompt}")

            if execution_results:
                parts.append(
                    "Tool execution results:\n"
                    + json.dumps(execution_results, indent=2, default=str)[:8000]
                )
            else:
                parts.append("No tools were executed for this request.")

            parts.append("Write the reply the user should see, grounded strictly in the data above.")

            response = await client.aio.models.generate_content(
                model=self.model,
                contents="\n\n".join(parts),
                config=types.GenerateContentConfig(
                    system_instruction=SYNTHESIS_SYSTEM_PROMPT,
                    temperature=0.2,
                ),
            )

            text = self._extract_text(response)
            if not text:
                return None
            return text, self._extract_usage(response)

        except Exception as exc:
            logger.warning("Gemini synthesis call failed (%s); falling back to template renderer.", exc)
            return None


_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
