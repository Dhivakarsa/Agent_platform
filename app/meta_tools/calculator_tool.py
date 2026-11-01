"""
Deterministic Safe Calculator & Time Tool for arithmetic and temporal reasoning.
Evaluates mathematical expressions safely using AST without dangerous eval().
"""

from __future__ import annotations

import ast
import datetime
import math
import operator
from typing import Any, Dict, Optional
import zoneinfo

# Safe binary operators mapping
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

SAFE_MATH_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
    "abs": abs,
    "round": round,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval_node(node: ast.AST) -> Any:
    """Recursively evaluates safe AST nodes for arithmetic."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in SAFE_OPERATORS:
            return SAFE_OPERATORS[op_type](_safe_eval_node(node.operand))
        raise ValueError(f"Unsupported unary operator: {op_type}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type in SAFE_OPERATORS:
            left = _safe_eval_node(node.left)
            right = _safe_eval_node(node.right)
            # Guard against excessive powers
            if op_type is ast.Pow and (right > 1000 or left > 100000):
                raise ValueError("Exponent or base too large for safe execution.")
            return SAFE_OPERATORS[op_type](left, right)
        raise ValueError(f"Unsupported binary operator: {op_type}")
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_MATH_FUNCS:
            func = SAFE_MATH_FUNCS[node.func.id]
            args = [_safe_eval_node(a) for a in node.args]
            return func(*args)
        raise ValueError("Unsupported function call")
    elif isinstance(node, ast.Name):
        if node.id in SAFE_MATH_FUNCS:
            return SAFE_MATH_FUNCS[node.id]
        raise ValueError(f"Unknown variable name: {node.id}")
    else:
        raise ValueError(f"Unsupported expression syntax: {type(node)}")


def safe_calculate(expression: str) -> Dict[str, Any]:
    """
    Safely evaluate a mathematical expression string.
    
    Example:
        safe_calculate("120 * 0.15 + 45.50")
    """
    cleaned = expression.strip().replace("^", "**")
    try:
        parsed = ast.parse(cleaned, mode="eval")
        result = _safe_eval_node(parsed)
        return {
            "status": "success",
            "expression": expression,
            "result": result,
            "formatted": f"{result:,.4f}".rstrip("0").rstrip(".") if isinstance(result, float) else str(result),
        }
    except Exception as exc:
        return {
            "status": "error",
            "expression": expression,
            "error": f"Evaluation error: {exc}",
        }


def get_current_time(timezone_str: str = "UTC") -> Dict[str, Any]:
    """
    Get current timestamp and date in specified timezone (e.g., 'UTC', 'America/New_York', 'Asia/Kolkata').
    """
    try:
        tz = zoneinfo.ZoneInfo(timezone_str)
        now = datetime.datetime.now(tz)
    except Exception:
        tz = datetime.timezone.utc
        timezone_str = "UTC"
        now = datetime.datetime.now(tz)

    return {
        "status": "success",
        "timezone": timezone_str,
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "timestamp_unix": int(now.timestamp()),
    }


def get_general_tool_definitions() -> list:
    """Return canonical RegisteredTool models for general-purpose calculation, time, and email tools."""
    from app.schemas.tool import ApprovalPolicy, ParameterDefinition, RegisteredTool, RiskLevel

    return [
        RegisteredTool(
            name="calculator_evaluate",
            display_name="Safe Calculator",
            description="Safely evaluate mathematical expressions and perform arithmetic without code execution risk.",
            category="general",
            namespace="general",
            method="POST",
            path="/tools/calculator",
            risk_level=RiskLevel.READ_ONLY,
            approval_policy=ApprovalPolicy.AUTO_APPROVED,
            capabilities=["math", "arithmetic", "percentage", "computation", "calculator"],
            parameters=[
                ParameterDefinition(
                    name="expression",
                    type="string",
                    description="Mathematical expression e.g. '120 * 0.15 + 45.50'",
                    required=True,
                )
            ],
            keywords=["calculate", "math", "add", "multiply", "divide", "sum", "total", "percentage", "rate"],
        ),
        RegisteredTool(
            name="time_get_current",
            display_name="Current Time & Timezone",
            description="Retrieve current date, time, day of the week, and timezone information.",
            category="general",
            namespace="general",
            method="GET",
            path="/tools/time",
            risk_level=RiskLevel.READ_ONLY,
            approval_policy=ApprovalPolicy.AUTO_APPROVED,
            capabilities=["time", "date", "timezone", "temporal", "current_time"],
            parameters=[
                ParameterDefinition(
                    name="timezone",
                    type="string",
                    description="IANA timezone name or 'UTC' e.g. 'UTC', 'America/New_York'",
                    required=False,
                    default="UTC",
                )
            ],
            keywords=["time", "date", "now", "today", "current time", "timezone", "day"],
        ),
        RegisteredTool(
            name="create_email_draft",
            display_name="Create Email Draft",
            description="Draft an email notification or summary for review. Reversible action, does not dispatch externally.",
            category="communication",
            namespace="communication",
            method="POST",
            path="/tools/email/draft",
            risk_level=RiskLevel.REVERSIBLE_WRITE,
            approval_policy=ApprovalPolicy.AUTO_APPROVED,
            capabilities=["email", "draft", "message", "communication"],
            parameters=[
                ParameterDefinition(name="to", type="string", description="Recipient email address", required=True),
                ParameterDefinition(name="subject", type="string", description="Email subject line", required=True),
                ParameterDefinition(name="body", type="string", description="Body text of email", required=True),
                ParameterDefinition(name="cc", type="string", description="Optional CC recipient", required=False),
            ],
            keywords=["email draft", "draft email", "compose email", "write email", "prepare message"],
        ),
        RegisteredTool(
            name="send_email",
            display_name="Send Outbound Email",
            description="Dispatch an email to an external recipient. High-risk EXTERNAL_WRITE action that requires HITL human approval.",
            category="communication",
            namespace="communication",
            method="POST",
            path="/tools/email/send",
            risk_level=RiskLevel.EXTERNAL_WRITE,
            approval_policy=ApprovalPolicy.REQUIRE_APPROVAL,
            approval_required=True,
            is_mutating=True,
            capabilities=["send_email", "dispatch_message", "notify_external"],
            parameters=[
                ParameterDefinition(name="to", type="string", description="Recipient email address", required=True),
                ParameterDefinition(name="subject", type="string", description="Email subject line", required=True),
                ParameterDefinition(name="body", type="string", description="Body text of email", required=True),
                ParameterDefinition(name="draft_id", type="string", description="Optional draft identifier to finalize", required=False),
            ],
            keywords=["send email", "dispatch email", "mail recipient", "email customer", "notify customer by email"],
        ),
        RegisteredTool(
            name="list_email_drafts",
            display_name="List Email Drafts",
            description="List all active pending email drafts.",
            category="communication",
            namespace="communication",
            method="GET",
            path="/tools/email/drafts",
            risk_level=RiskLevel.READ_ONLY,
            approval_policy=ApprovalPolicy.AUTO_APPROVED,
            capabilities=["email_drafts", "list_drafts"],
            parameters=[],
            keywords=["list drafts", "pending drafts", "view drafts", "email drafts"],
        ),
    ]
