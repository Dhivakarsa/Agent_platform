"""
Sandboxed HTTP execution engine with audit logging and idempotency control.

Validates input parameters, attaches PayPal-Request-Id idempotency headers,
dispatches HTTP requests or executes realistic financial mocks, and writes audit logs.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional
import httpx

from app.config import get_settings
from app.db.models import ExecutionLog
from app.db.session import async_session_factory
from app.schemas.tool import ExecutionStatus, RegisteredTool, ToolCallResult

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Dispatches tool executions in mock or sandbox/live HTTP environments."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def execute_tool(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        session_id: str,
        request_id: Optional[str] = None,
        user_id: str = "system",
        user_role: str = "user",
    ) -> ToolCallResult:
        """
        Execute tool with parameter validation, idempotency header injection,
        sandboxed HTTP or realistic mock dispatch, and audit logging.

        ``user_id`` is recorded on the audit row so per-user log isolation and the
        administrator's per-user drilldown can filter on it.
        """
        start_time = time.perf_counter()
        req_id = request_id or str(uuid.uuid4())
        logger.info("Executing tool %s [session=%s, req_id=%s]", tool.name, session_id, req_id)

        status = ExecutionStatus.SUCCESS
        http_code = 200
        error_msg = None
        response_payload: dict[str, Any] = {}

        try:
            # 1. Parameter Validation
            self._validate_parameters(tool, arguments)

            # 2. Dispatch
            is_mcp = (
                getattr(tool, "source_type", None) == "mcp"
                or bool(getattr(tool, "mcp_server_name", None))
                or tool.name.startswith("mcp_")
            )

            if is_mcp:
                response_payload, http_code = await self._mcp_dispatch(tool, arguments, req_id)
            elif self.settings.execution_mode == "mock" or not self.settings.paypal_client_id:
                response_payload, http_code = await self._mock_dispatch(
                    tool, arguments, req_id, user_id=user_id, user_role=user_role
                )
            else:
                response_payload, http_code = await self._http_dispatch(tool, arguments, req_id)

        except ValueError as val_err:
            status = ExecutionStatus.VALIDATION_ERROR
            http_code = 400
            error_msg = f"Validation failed: {val_err}"
            response_payload = {"error": "VALIDATION_ERROR", "details": str(val_err)}
            logger.warning("Tool %s validation failed: %s", tool.name, val_err)

        except httpx.HTTPStatusError as http_err:
            status = ExecutionStatus.HTTP_ERROR
            http_code = http_err.response.status_code
            error_msg = f"HTTP {http_code}: {http_err.response.text}"
            response_payload = {"error": "HTTP_ERROR", "status_code": http_code, "details": error_msg}
            logger.error("HTTP error for tool %s: %s", tool.name, error_msg)

        except Exception as exc:
            status = ExecutionStatus.INTERNAL_ERROR
            http_code = 500
            error_msg = str(exc)
            response_payload = {"error": "INTERNAL_ERROR", "details": str(exc)}
            logger.error("Unexpected error executing tool %s: %s", tool.name, exc)

        latency_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

        # 3. Write relational audit log
        await self._log_execution(
            user_id=user_id,
            session_id=session_id,
            request_id=req_id,
            tool_name=tool.name,
            status=status.value,
            input_args=arguments,
            response_payload=response_payload,
            latency_ms=latency_ms,
            error_message=error_msg,
        )

        return ToolCallResult(
            tool_name=tool.name,
            session_id=session_id,
            status=status,
            http_status_code=http_code,
            request_id=req_id,
            input_arguments=arguments,
            response_payload=response_payload,
            error_message=error_msg,
            latency_ms=latency_ms,
        )

    def _validate_parameters(self, tool: RegisteredTool, arguments: dict[str, Any]) -> None:
        """Verify that required parameters are supplied."""
        missing = []
        for param in tool.parameters:
            if param.required and param.name not in arguments:
                missing.append(param.name)
        if missing:
            raise ValueError(f"Missing required arguments: {', '.join(missing)}")

    async def _mock_dispatch(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        request_id: str,
        user_id: str = "system",
        user_role: str = "user",
    ) -> tuple[dict[str, Any], int]:
        """Produce deterministic, realistic financial responses for PayPal operations."""
        name = tool.name
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Internal Meta-Tools & Calculator
        if name == "calculator_evaluate":
            from app.meta_tools.calculator_tool import safe_calculate
            expr = str(arguments.get("expression") or arguments.get("query") or "0")
            return safe_calculate(expr), 200

        if name == "time_get_current":
            from app.meta_tools.calculator_tool import get_current_time
            tz = str(arguments.get("timezone") or arguments.get("timezone_str") or "UTC")
            return get_current_time(tz), 200

        if name == "create_email_draft":
            from app.meta_tools.email_tool import create_email_draft
            return create_email_draft(
                to=str(arguments.get("to") or arguments.get("recipient") or ""),
                subject=str(arguments.get("subject", "Automated Notice")),
                body=str(arguments.get("body", "")),
                cc=arguments.get("cc"),
            ), 200

        if name == "send_email":
            from app.meta_tools.email_tool import send_email
            return send_email(
                to=str(arguments.get("to") or arguments.get("recipient") or ""),
                subject=str(arguments.get("subject", "Automated Notice")),
                body=str(arguments.get("body", "")),
                draft_id=arguments.get("draft_id"),
            ), 200

        if name == "list_email_drafts":
            from app.meta_tools.email_tool import list_email_drafts
            return list_email_drafts(), 200

        if name in ("rag_pipeline_tool", "search_paypal_documentation"):
            from app.meta_tools.rag_pipeline_tool import get_rag_tool
            rag = get_rag_tool()
            return await rag.query(
                query=str(arguments.get("query") or arguments.get("question") or ""),
                domain_filter=arguments.get("domain_filter") or None,
            ), 200

        if name in ("system_search_tool", "system_catalog_search"):
            from app.meta_tools.system_search_tool import get_system_search_tool
            st = get_system_search_tool()
            scope = str(arguments.get("scope") or "capabilities")
            if scope not in ("capabilities", "execution_logs"):
                scope = "capabilities"
            try:
                limit = int(arguments.get("limit") or 5)
            except (TypeError, ValueError):
                limit = 5
            return await st.query(
                scope=scope,
                query=str(arguments.get("query") or ""),
                limit=limit,
                session_id=arguments.get("session_id"),
                user_id=user_id,
                user_role=user_role,
            ), 200

        # Invoicing responses
        if "generate_invoice_number" in name or "next_invoice_number" in name:
            return {"invoice_number": f"INV2-{int(time.time()) % 10000:04d}"}, 200

        if "create_draft_invoice" in name or ("create" in name and "invoice" in name):
            inv_id = f"INV2-{uuid.uuid4().hex[:8].upper()}"
            recip = arguments.get("recipient_email") or arguments.get("email") or arguments.get("to") or "client@example.com"
            amt = str(arguments.get("amount", "50.00"))
            curr = str(arguments.get("currency_code", "USD"))
            inv_num = arguments.get("invoice_number", f"INV2-{int(time.time()) % 10000:04d}")
            return {
                "id": inv_id,
                "status": "DRAFT",
                "invoice_number": inv_num,
                "recipient_email": recip,
                "primary_recipients": [{"billing_info": {"email_address": recip}}],
                "amount": {"currency_code": curr, "value": amt},
                "create_time": timestamp,
            }, 201

        if "send_invoice" in name:
            inv_id = arguments.get("invoice_id", f"INV2-{uuid.uuid4().hex[:8].upper()}")
            recip = (
                arguments.get("recipient_email")
                or arguments.get("email")
                or arguments.get("to")
            )
            if not recip:
                raise ValueError("send_invoice requires a recipient email address")
            amt = str(arguments.get("amount", "50.00"))
            curr = str(arguments.get("currency_code", "USD"))

            # Dispatch real outbound email via Google SMTP
            email_res = {}
            try:
                from app.meta_tools.email_tool import send_email
                subject = f"Invoice {inv_id} for ${amt} {curr}"
                body = (
                    f"Dear Customer,\n\n"
                    f"Please find your invoice details below:\n"
                    f"----------------------------------------\n"
                    f"Invoice ID:   {inv_id}\n"
                    f"Amount Due:   ${amt} {curr}\n"
                    f"Status:       SENT / ISSUED\n"
                    f"Payment Due:  Upon Receipt (30-day terms)\n"
                    f"----------------------------------------\n\n"
                    f"You can view and settle this invoice via your PayPal portal.\n\n"
                    f"Thank you for your business!\n"
                    f"Accounts Receivable"
                )
                email_res = send_email(to=recip, subject=subject, body=body)
            except Exception as e:
                email_res = {"status": "error", "error": str(e), "real_smtp_delivery": False}

            return {
                "id": inv_id,
                "status": "SENT",
                "invoice_number": inv_id,
                "recipient_email": recip,
                "amount": {"currency_code": curr, "value": amt},
                "message": f"Invoice {inv_id} sent successfully to {recip}.",
                "send_time": timestamp,
                "email_delivery": email_res,
                "real_smtp_delivery": email_res.get("real_smtp_delivery", False),
            }, 202

        # Order responses
        if "create_order" in name:
            order_id = f"ORDER-{uuid.uuid4().hex[:8].upper()}"
            return {
                "id": order_id,
                "status": "CREATED",
                "intent": arguments.get("intent", "CAPTURE"),
                "purchase_units": [{"amount": {"currency_code": "USD", "value": str(arguments.get("amount", "100.00"))}}],
                "create_time": timestamp,
            }, 201

        if "capture_order" in name:
            order_id = arguments.get("order_id", "ORDER-DEMO-123")
            return {
                "id": order_id,
                "status": "COMPLETED",
                "capture_id": f"CAP-{uuid.uuid4().hex[:8].upper()}",
                "amount": {"currency_code": "USD", "value": "100.00"},
            }, 200

        # Payments / refunds
        if "refund" in name:
            return {
                "id": f"REF-{uuid.uuid4().hex[:8].upper()}",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": str(arguments.get("amount", "50.00"))},
                "create_time": timestamp,
            }, 201

        # Disputes
        if "dispute" in name:
            if "list" in name:
                return {
                    "items": [
                        {
                            "dispute_id": "PP-D-12345",
                            "create_time": timestamp,
                            "dispute_amount": {"currency_code": "USD", "value": "45.00"},
                            "status": "UNDER_REVIEW",
                            "disputed_transaction": {"seller_transaction_id": "TX-9988"},
                            "dispute_reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                        }
                    ],
                    "total_items": 1,
                }, 200
            return {"dispute_id": "PP-D-12345", "status": "RESOLVED"}, 200

        # Reporting
        if "transaction" in name or "reporting" in name or "balance" in name:
            return {
                "total_items": 15,
                "total_sales_volume": {"currency_code": "USD", "value": "4250.75"},
                "total_fee": {"currency_code": "USD", "value": "127.50"},
                "transaction_details": [
                    {"transaction_id": f"TX-{i}", "amount": {"value": "150.00", "currency_code": "USD"}, "status": "S"}
                    for i in range(1, 4)
                ],
            }, 200

        # Generic fallback mock
        return {
            "status": "SUCCESS",
            "tool": name,
            "idempotency_key": request_id,
            "result": f"Executed {name} successfully.",
            "data": arguments,
        }, 200

    async def _http_dispatch(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        request_id: str,
    ) -> tuple[dict[str, Any], int]:
        """Dispatch real HTTP call to PayPal API endpoint with idempotency headers."""
        headers = {
            "PayPal-Request-Id": request_id,
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        url = f"{self.settings.paypal_base_url}{tool.path}"

        # Resolve path variables
        for k, v in list(arguments.items()):
            if f"{{{k}}}" in url:
                url = url.replace(f"{{{k}}}", str(v))

        async with httpx.AsyncClient(timeout=self.settings.default_api_timeout) as client:
            resp = await client.request(
                method=tool.method,
                url=url,
                json=arguments if tool.method in ["POST", "PUT", "PATCH"] else None,
                params=arguments if tool.method == "GET" else None,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            return data, resp.status_code

    async def _mcp_dispatch(
        self,
        tool: RegisteredTool,
        arguments: dict[str, Any],
        request_id: str,
    ) -> tuple[dict[str, Any], int]:
        """Dispatch tool execution to the MCP Client Service."""
        from app.services.mcp_client import get_mcp_service

        mcp_service = get_mcp_service()
        server_name = getattr(tool, "mcp_server_name", None)
        if not server_name:
            parts = tool.name.split("_")
            if len(parts) >= 3 and parts[0] == "mcp":
                server_name = parts[1]
            else:
                server_name = "filesystem"

        remote_name = getattr(tool, "remote_tool_name", None) or tool.name
        result = await mcp_service.execute_tool(server_name, remote_name, arguments)
        http_code = 200 if "error" not in result else 400
        return result, http_code

    async def _log_execution(
        self,
        user_id: str,
        session_id: str,
        request_id: str,
        tool_name: str,
        status: str,
        input_args: dict[str, Any],
        response_payload: Optional[dict[str, Any]],
        latency_ms: float,
        error_message: Optional[str],
    ) -> None:
        """Persist execution log entry to relational database."""
        try:
            async with async_session_factory() as s:
                entry = ExecutionLog(
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    tool_name=tool_name,
                    status=status,
                    input_args=input_args,
                    response_payload=response_payload,
                    latency_ms=latency_ms,
                    error_message=error_message,
                )
                s.add(entry)
                await s.commit()
        except Exception as exc:
            logger.warning("Failed to write audit log: %s", exc)


_execution_engine: Optional[ExecutionEngine] = None


def get_execution_engine() -> ExecutionEngine:
    global _execution_engine
    if _execution_engine is None:
        _execution_engine = ExecutionEngine()
    return _execution_engine

