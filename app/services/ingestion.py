"""
Postman Collection & OpenAPI v3 JSON ingestion pipeline and synthetic catalog generator.

Converts Postman collection JSON and OpenAPI specs into typed RegisteredTool models,
extracting paths, methods, schemas, synthetic intent keywords, and dependency DAG edges.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.schemas.tool import HttpMethod, ParameterSchema, RegisteredTool, ToolDomain

logger = logging.getLogger(__name__)

# Known prior tool dependencies across the PayPal operational workflows
DEPENDENCY_RULES = {
    "send_invoice": ["create_draft_invoice"],
    "record_payment_invoice": ["send_invoice"],
    "cancel_invoice": ["send_invoice"],
    "delete_draft_invoice": ["create_draft_invoice"],
    "capture_order": ["create_order"],
    "authorize_order": ["create_order"],
    "capture_payment": ["authorize_payment"],
    "refund_payment": ["capture_payment"],
    "void_payment": ["authorize_payment"],
    "accept_dispute": ["list_disputes"],
    "appeal_dispute": ["list_disputes"],
    "provide_evidence": ["list_disputes"],
    "activate_subscription": ["create_subscription"],
    "cancel_subscription": ["create_subscription"],
    "capture_subscription_payment": ["create_subscription"],
}

# Domain keyword mapping for classifying query intents
DOMAIN_KEYWORDS = {
    "invoicing": ["invoice", "bill", "billing", "draft", "itemized", "send invoice", "reminder"],
    "orders": ["order", "checkout", "cart", "purchase", "authorize order", "capture order"],
    "payments": ["payment", "capture", "refund", "reauthorize", "void", "money", "transaction", "pay"],
    "disputes": ["dispute", "claim", "chargeback", "appeal", "evidence", "buyer conflict", "contest"],
    "reporting": ["report", "sales volume", "analytics", "history", "balance", "transactions", "statement"],
    "subscriptions": ["subscription", "recurring", "billing plan", "monthly", "annual plan"],
    "payouts": ["payout", "batch payout", "mass payment", "disbursement", "transfer"],
    "webhooks": ["webhook", "notification", "event", "callback", "listener"],
    "vault": ["vault", "payment token", "credit card", "saved card", "payment method"],
    "tracking": ["shipment", "tracker", "tracking number", "carrier", "fulfillment"],
}


class PostmanCollectionIngester:
    """Ingests Postman Collection v2.1 JSON into structured RegisteredTool records."""

    def __init__(self, collection_data: Optional[dict[str, Any]] = None, file_path: Optional[str] = None):
        if file_path:
            with open(file_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        elif collection_data:
            self.data = collection_data
        else:
            self.data = {}

    def parse_collection(self) -> list[RegisteredTool]:
        """Traverse folders and requests to extract RegisteredTool instances."""
        tools: list[RegisteredTool] = []
        items = self.data.get("item", [])
        self._traverse_items(items, parent_domain="", tools=tools)
        logger.info("Ingested %d tools from Postman collection", len(tools))
        return tools

    def _traverse_items(
        self,
        items: list[dict[str, Any]],
        parent_domain: str,
        tools: list[RegisteredTool],
    ) -> None:
        for item in items:
            # If folder has nested items
            if "item" in item:
                folder_name = item.get("name", "").lower()
                domain = self._normalize_domain(folder_name) or parent_domain or "general"
                self._traverse_items(item["item"], domain, tools)
            elif "request" in item:
                tool = self._parse_request_item(item, parent_domain)
                if tool:
                    tools.append(tool)

    def _parse_request_item(self, item: dict[str, Any], current_domain: str) -> Optional[RegisteredTool]:
        name_raw = item.get("name", "")
        request = item.get("request", {})
        if not request:
            return None

        method = request.get("method", "GET").upper()
        url_obj = request.get("url", {})
        path = self._extract_path(url_obj)

        domain = current_domain or self._infer_domain_from_path(path)
        tool_name = self._generate_tool_name(name_raw, domain)

        description = item.get("description", "")
        if isinstance(description, dict):
            description = description.get("content", "")
        if not description:
            description = f"Performs {method} on {path} to {name_raw}."

        parameters = self._extract_parameters(request)
        keywords = self._generate_keywords(tool_name, name_raw, domain)
        requires_prior = self._detect_dependencies(tool_name)
        is_mutating = method in ["POST", "PUT", "PATCH", "DELETE"]

        return RegisteredTool(
            name=tool_name,
            domain=domain,
            method=method,
            path=path,
            description=description,
            keywords=keywords,
            parameters=parameters,
            is_mutating=is_mutating,
            requires_prior=requires_prior,
            source="postman",
        )

    def _extract_path(self, url_obj: Any) -> str:
        if isinstance(url_obj, str):
            # Extract path from raw URL
            parts = url_obj.split("paypal.com")
            return parts[-1] if len(parts) > 1 else url_obj
        if isinstance(url_obj, dict):
            raw = url_obj.get("raw", "")
            if raw:
                parts = raw.split("paypal.com")
                return parts[-1].split("?")[0] if len(parts) > 1 else raw
            path_segments = url_obj.get("path", [])
            return "/" + "/".join(str(s) for s in path_segments)
        return ""

    def _extract_parameters(self, request: dict[str, Any]) -> list[ParameterSchema]:
        params: list[ParameterSchema] = []

        # 1. URL Path parameters
        url_obj = request.get("url", {})
        if isinstance(url_obj, dict):
            for var in url_obj.get("variable", []):
                params.append(
                    ParameterSchema(
                        name=var.get("key", "id"),
                        type="string",
                        description=var.get("description", f"Path variable {var.get('key')}"),
                        required=True,
                        location="path",
                    )
                )

            # 2. Query parameters
            for query in url_obj.get("query", []):
                params.append(
                    ParameterSchema(
                        name=query.get("key", ""),
                        type="string",
                        description=query.get("description", f"Query parameter {query.get('key')}"),
                        required=not query.get("disabled", False),
                        location="query",
                    )
                )

        # 3. Body parameters (JSON)
        body = request.get("body", {})
        if body.get("mode") == "raw" and "raw" in body:
            try:
                parsed_json = json.loads(body["raw"])
                if isinstance(parsed_json, dict):
                    for k, v in parsed_json.items():
                        v_type = "string"
                        if isinstance(v, (int, float)):
                            v_type = "number"
                        elif isinstance(v, bool):
                            v_type = "boolean"
                        elif isinstance(v, dict):
                            v_type = "object"
                        elif isinstance(v, list):
                            v_type = "array"

                        params.append(
                            ParameterSchema(
                                name=k,
                                type=v_type,
                                description=f"Body payload field '{k}'",
                                required=False,
                                location="body",
                            )
                        )
            except Exception:
                pass

        return params

    def _normalize_domain(self, folder_name: str) -> str:
        for domain in DOMAIN_KEYWORDS.keys():
            if domain in folder_name:
                return domain
        return "general"

    def _infer_domain_from_path(self, path: str) -> str:
        for domain in DOMAIN_KEYWORDS.keys():
            if domain in path.lower():
                return domain
        return "general"

    def _generate_tool_name(self, name_raw: str, domain: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9\s]", "", name_raw).strip().lower()
        snake = "_".join(clean.split())
        if not snake.startswith("paypal_"):
            snake = f"paypal_{snake}"
        return snake[:60]

    def _generate_keywords(self, tool_name: str, name_raw: str, domain: str) -> list[str]:
        words = set(re.findall(r"\w+", f"{tool_name} {name_raw} {domain}".lower()))
        domain_extras = DOMAIN_KEYWORDS.get(domain, [])
        words.update(domain_extras)
        return sorted(list(words))

    def _detect_dependencies(self, tool_name: str) -> list[str]:
        prior = []
        for action, required_priors in DEPENDENCY_RULES.items():
            if action in tool_name:
                for req in required_priors:
                    prior.append(f"paypal_{req}")
        return prior


def create_synthetic_tools(count: int, external_domains: Optional[list[str]] = None) -> list[RegisteredTool]:
    """
    Generate synthetic tools for benchmark scaling to 100, 500, or 1,000+ endpoints.
    Simulates Stripe, Slack, Jira, GitHub, AWS, and HubSpot tools with variable schemas.
    """
    domains = external_domains or ["payments", "messaging", "project_management", "version_control", "cloud_infra", "crm"]
    services = {
        "payments": ("stripe", ["create_charge", "refund_charge", "create_customer", "retrieve_balance", "list_disputes"]),
        "messaging": ("slack", ["send_message", "create_channel", "upload_file", "archive_channel", "list_users"]),
        "project_management": ("jira", ["create_issue", "update_issue_status", "assign_ticket", "search_issues_jql", "add_comment"]),
        "version_control": ("github", ["create_pull_request", "merge_pr", "list_commits", "create_issue", "add_collaborator"]),
        "cloud_infra": ("aws", ["launch_ec2_instance", "terminate_instance", "create_s3_bucket", "put_s3_object", "query_cloudwatch"]),
        "crm": ("hubspot", ["create_contact", "update_deal_stage", "log_call", "search_company", "send_marketing_email"]),
    }

    tools: list[RegisteredTool] = []
    generated = 0

    while generated < count:
        for domain in domains:
            if generated >= count:
                break
            svc_prefix, base_actions = services.get(domain, ("service", ["action_one", "action_two"]))
            action_idx = generated % len(base_actions)
            action_base = base_actions[action_idx]
            tool_name = f"{svc_prefix}_{action_base}_{generated}"

            method = "POST" if ("create" in action_base or "launch" in action_base or "send" in action_base) else "GET"
            is_mutating = method == "POST"

            parameters = [
                ParameterSchema(name="query_id", type="string", description="Identifier parameter", required=True, location="query"),
                ParameterSchema(name="details", type="string", description="Operational payload notes", required=False, location="body"),
            ]
            if is_mutating:
                parameters.append(ParameterSchema(name="amount_or_count", type="number", description="Numeric quantity", required=False, location="body"))

            tools.append(
                RegisteredTool(
                    name=tool_name,
                    domain=domain,
                    method=method,
                    path=f"/v1/{svc_prefix}/{action_base}/{generated}",
                    description=f"{svc_prefix.capitalize()} action: {action_base.replace('_', ' ')} with index {generated}.",
                    keywords=[svc_prefix, action_base.replace("_", " "), domain],
                    parameters=parameters,
                    is_mutating=is_mutating,
                    source="synthetic",
                )
            )
            generated += 1

    return tools


def generate_full_paypal_collection() -> dict[str, Any]:
    """
    Generates a full 50+ real PayPal API Postman collection dataset
    matching the official PayPal Public API Workspace.
    """
    return {
        "info": {
            "name": "PayPal APIs (Complete 50+ Endpoints)",
            "description": "Comprehensive PayPal API collection covering Invoicing, Orders, Payments, Disputes, Reporting, Subscriptions, Payouts, Webhooks, Vault, and Trackers.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": [
            {
                "name": "Invoicing",
                "item": [
                    {"name": "Generate Invoice Number", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/generate-next-invoice-number"}},
                    {"name": "Create Draft Invoice", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices", "body": {"mode": "raw", "raw": "{\"detail\":{\"currency_code\":\"USD\"},\"invoicer\":{\"email_address\":\"merchant@example.com\"},\"primary_recipients\":[{\"billing_info\":{\"email_address\":\"client@example.com\"}}],\"items\":[{\"name\":\"Consulting\",\"quantity\":\"1\",\"unit_amount\":{\"currency_code\":\"USD\",\"value\":\"50.00\"}}]}"}}},
                    {"name": "Send Invoice", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234/send"}},
                    {"name": "Show Invoice Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234"}},
                    {"name": "List Invoices", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices?page=1&page_size=10"}},
                    {"name": "Update Invoice", "request": {"method": "PUT", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234"}},
                    {"name": "Delete Draft Invoice", "request": {"method": "DELETE", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234"}},
                    {"name": "Cancel Sent Invoice", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234/cancel"}},
                    {"name": "Record Payment For Invoice", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234/payments"}},
                    {"name": "Send Invoice Reminder", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-1234/remind"}}
                ]
            },
            {
                "name": "Orders",
                "item": [
                    {"name": "Create Order", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/checkout/orders", "body": {"mode": "raw", "raw": "{\"intent\":\"CAPTURE\",\"purchase_units\":[{\"amount\":{\"currency_code\":\"USD\",\"value\":\"100.00\"}}]}"}}},
                    {"name": "Show Order Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/checkout/orders/ORDER-123"}},
                    {"name": "Update Order", "request": {"method": "PATCH", "url": "https://api-m.sandbox.paypal.com/v2/checkout/orders/ORDER-123"}},
                    {"name": "Authorize Order", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/checkout/orders/ORDER-123/authorize"}},
                    {"name": "Capture Order", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/checkout/orders/ORDER-123/capture"}}
                ]
            },
            {
                "name": "Payments",
                "item": [
                    {"name": "Show Authorized Payment Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/payments/authorizations/AUTH-123"}},
                    {"name": "Capture Authorized Payment", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/payments/authorizations/AUTH-123/capture"}},
                    {"name": "Reauthorize Authorized Payment", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/payments/authorizations/AUTH-123/reauthorize"}},
                    {"name": "Void Authorized Payment", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/payments/authorizations/AUTH-123/void"}},
                    {"name": "Show Captured Payment Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP-123"}},
                    {"name": "Refund Captured Payment", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP-123/refund", "body": {"mode": "raw", "raw": "{\"amount\":{\"value\":\"50.00\",\"currency_code\":\"USD\"},\"note_to_payer\":\"Refund request\"}"}}},
                    {"name": "Show Refunded Payment Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/payments/refunds/REF-123"}}
                ]
            },
            {
                "name": "Disputes",
                "item": [
                    {"name": "List Disputes", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/customer/disputes?dispute_state=REQUIRED_ACTION"}},
                    {"name": "Show Dispute Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/customer/disputes/DISPUTE-123"}},
                    {"name": "Accept Dispute Claim", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/customer/disputes/DISPUTE-123/accept-claim"}},
                    {"name": "Appeal Dispute", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/customer/disputes/DISPUTE-123/appeal"}},
                    {"name": "Provide Dispute Evidence", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/customer/disputes/DISPUTE-123/provide-evidence"}},
                    {"name": "Settle Dispute", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/customer/disputes/DISPUTE-123/settle"}}
                ]
            },
            {
                "name": "Reporting",
                "item": [
                    {"name": "List Transactions", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/reporting/transactions?start_date=2026-10-01T00:00:00Z&end_date=2026-10-31T23:59:59Z"}},
                    {"name": "List Balances", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/reporting/balances?as_of_time=2026-10-31T23:59:59Z"}},
                    {"name": "Get Transaction Report", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/reporting/transactions"}},
                    {"name": "Generate Settlement Report", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/reporting/settlement-reports"}}
                ]
            },
            {
                "name": "Subscriptions",
                "item": [
                    {"name": "Create Billing Plan", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/plans"}},
                    {"name": "List Billing Plans", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/billing/plans"}},
                    {"name": "Show Billing Plan Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/billing/plans/P-123"}},
                    {"name": "Activate Billing Plan", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/plans/P-123/activate"}},
                    {"name": "Deactivate Billing Plan", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/plans/P-123/deactivate"}},
                    {"name": "Create Subscription", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/subscriptions"}},
                    {"name": "Show Subscription Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/billing/subscriptions/I-123"}},
                    {"name": "Activate Subscription", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/subscriptions/I-123/activate"}},
                    {"name": "Cancel Subscription", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/subscriptions/I-123/cancel"}},
                    {"name": "Capture Subscription Payment", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/subscriptions/I-123/capture"}},
                    {"name": "Revise Subscription", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/billing/subscriptions/I-123/revise"}}
                ]
            },
            {
                "name": "Payouts",
                "item": [
                    {"name": "Create Batch Payout", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/payments/payouts", "body": {"mode": "raw", "raw": "{\"sender_batch_header\":{\"sender_batch_id\":\"batch_123\"},\"items\":[{\"recipient_type\":\"EMAIL\",\"amount\":{\"value\":\"10.00\",\"currency\":\"USD\"},\"receiver\":\"user@example.com\"}]}"}}},
                    {"name": "Show Payout Batch Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/payments/payouts/PAYOUT-BATCH-123"}},
                    {"name": "Show Payout Item Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/payments/payouts-item/ITEM-123"}},
                    {"name": "Cancel Unclaimed Payout Item", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/payments/payouts-item/ITEM-123/cancel"}}
                ]
            },
            {
                "name": "Webhooks",
                "item": [
                    {"name": "List Webhooks", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/notifications/webhooks"}},
                    {"name": "Create Webhook", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/notifications/webhooks"}},
                    {"name": "Show Webhook Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/notifications/webhooks/WH-123"}},
                    {"name": "Delete Webhook", "request": {"method": "DELETE", "url": "https://api-m.sandbox.paypal.com/v1/notifications/webhooks/WH-123"}},
                    {"name": "Verify Webhook Signature", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature"}},
                    {"name": "Simulate Webhook Event", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/notifications/simulate-event"}}
                ]
            },
            {
                "name": "Vault",
                "item": [
                    {"name": "Create Payment Token", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v2/vault/payment-tokens"}},
                    {"name": "List Payment Tokens", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/vault/payment-tokens?customer_id=CUST-123"}},
                    {"name": "Show Payment Token Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v2/vault/payment-tokens/TOKEN-123"}},
                    {"name": "Delete Payment Token", "request": {"method": "DELETE", "url": "https://api-m.sandbox.paypal.com/v2/vault/payment-tokens/TOKEN-123"}}
                ]
            },
            {
                "name": "Tracking",
                "item": [
                    {"name": "Add Tracking Info", "request": {"method": "POST", "url": "https://api-m.sandbox.paypal.com/v1/shipping/trackers"}},
                    {"name": "Show Tracker Details", "request": {"method": "GET", "url": "https://api-m.sandbox.paypal.com/v1/shipping/trackers/TRACKER-123"}},
                    {"name": "Update Tracker", "request": {"method": "PUT", "url": "https://api-m.sandbox.paypal.com/v1/shipping/trackers/TRACKER-123"}}
                ]
            }
        ]
    }

