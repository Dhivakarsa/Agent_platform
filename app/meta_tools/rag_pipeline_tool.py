"""
Pinned Meta-Tool 1: RAG Pipeline Tool.

Retrieves developer documentation, error codes, validation constraints, and ISO specifications
from the dedicated Qdrant 'knowledge_base' collection.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.config import get_settings
from app.db.vector_store import VectorStoreService, generate_deterministic_embedding, get_vector_store
from app.schemas.tool import ParameterSchema, RegisteredTool

logger = logging.getLogger(__name__)

# Canonical PayPal reference documents indexed for RAG
KNOWLEDGE_DOCS = [
    {
        "id": "doc_refund_reasons",
        "title": "PayPal Refund Reason Codes",
        "domain": "payments",
        "content": (
            "Valid PayPal refund reason codes include: "
            "1. 'BUYER_COMPLAINT' - Buyer lodged an issue or dispute. "
            "2. 'SELLER_FAULT' - Issue with merchant fulfillment or defective merchandise. "
            "3. 'RETURN' - Product returned by customer per return policy. "
            "4. 'OTHER' - Unspecified mutual agreement for reimbursement. "
            "Refunds on captures must specify 'amount' object with 'value' and 'currency_code'."
        ),
    },
    {
        "id": "doc_error_422",
        "title": "HTTP 422 UNPROCESSABLE_ENTITY Troubleshooting",
        "domain": "general",
        "content": (
            "HTTP 422 occurs when the payload is syntactically valid JSON but violates business rules. "
            "Common causes: "
            "1. CURRENCY_NOT_ALLOWED: Specified currency code is unsupported for this merchant. "
            "2. INVALID_CURRENCY_AMOUNT: Decimal precision exceeds allowed scale (e.g. 3 decimal places for USD). "
            "3. DUPLICATE_INVOICE_NUMBER: The generated invoice number is already in use. Call generate_invoice_number. "
            "4. MISSING_REQUIRED_RECIPIENT: Invoicing requires primary_recipients with valid billing_info.email_address."
        ),
    },
    {
        "id": "doc_invoicing_workflow",
        "title": "PayPal Invoicing Multi-Step Lifecycle",
        "domain": "invoicing",
        "content": (
            "The standard PayPal Invoicing lifecycle requires: "
            "Step 1: Optionally call 'generate_invoice_number' to obtain the next sequential identifier. "
            "Step 2: Call 'create_draft_invoice' passing items, currency, invoicer, and primary recipient email. "
            "Step 3: Call 'send_invoice' with the returned invoice_id ('INV2-...') to notify the buyer and enable payment. "
            "Invoices cannot be paid until transitioning from DRAFT to SENT status."
        ),
    },
    {
        "id": "doc_iso_currencies",
        "title": "Supported ISO 4217 Currency Codes",
        "domain": "payments",
        "content": (
            "PayPal standard accounts support the following ISO 4217 currencies: "
            "USD (US Dollar), EUR (Euro), GBP (British Pound), CAD (Canadian Dollar), "
            "AUD (Australian Dollar), JPY (Japanese Yen, integer only), CHF (Swiss Franc). "
            "All amounts for USD/EUR must have two decimal places (e.g. '50.00')."
        ),
    },
    {
        "id": "doc_disputes_flow",
        "title": "PayPal Dispute Resolution Process",
        "domain": "disputes",
        "content": (
            "PayPal disputes lifecycle: "
            "1. Inquiry / REQUIRED_ACTION state: Merchant can review buyer claim details via 'list_disputes'. "
            "2. Evidence submission: Merchant submits tracking or signature via 'provide_evidence'. "
            "3. Acceptance: Merchant can concede via 'accept_claim' to immediately refund buyer. "
            "4. Appeal: If decided against seller, merchant may submit 'appeal_dispute' within 10 days."
        ),
    },
    {
        "id": "doc_idempotency",
        "title": "PayPal API Idempotency and Headers",
        "domain": "general",
        "content": (
            "All mutating requests (POST, PUT, PATCH) should include the 'PayPal-Request-Id' header "
            "with a unique UUID. If a network timeout or retry occurs, passing the same PayPal-Request-Id "
            "guarantees the action is processed only once, preventing double charges or duplicate invoices."
        ),
    },
]


class RAGPipelineTool:
    """Pinned meta-tool executing vector search against documentation and guides."""

    def __init__(self, vector_store: Optional[VectorStoreService] = None) -> None:
        self.settings = get_settings()
        self.vector_store = vector_store or get_vector_store()
        self.collection_name = self.settings.qdrant_knowledge_collection
        self._ensure_documents_indexed()

    def _ensure_documents_indexed(self) -> None:
        """Seed the knowledge base collection if empty."""
        try:
            if self.vector_store.count(self.collection_name) == 0:
                logger.info("Indexing initial RAG knowledge base documents...")
                points = []
                for doc in KNOWLEDGE_DOCS:
                    text = f"{doc['title']}\n{doc['content']}"
                    vec = generate_deterministic_embedding(text, self.settings.embedding_dimension)
                    pt_id = int(uuid.uuid5(uuid.NAMESPACE_DNS, doc["id"]).int % 0x7FFFFFFF)
                    points.append(
                        {
                            "id": pt_id,
                            "vector": vec,
                            "payload": {
                                "doc_id": doc["id"],
                                "title": doc["title"],
                                "domain": doc["domain"],
                                "content": doc["content"],
                            },
                        }
                    )
                self.vector_store.upsert_vectors(self.collection_name, points)
                logger.info("Indexed %d knowledge base documents", len(points))
        except Exception as exc:
            logger.warning("Error indexing RAG knowledge docs: %s", exc)

    async def query(self, query: str, domain_filter: Optional[str] = None, limit: int = 3) -> dict[str, Any]:
        """Query knowledge base for error codes, developer guides, or API specifications."""
        query_vec = generate_deterministic_embedding(query, self.settings.embedding_dimension)
        scored_points = self.vector_store.search_vectors(
            collection_name=self.collection_name,
            query_vector=query_vec,
            limit=limit,
            domain_filter=domain_filter,
        )

        matches = []
        for pt in scored_points:
            if pt.payload:
                matches.append(
                    {
                        "title": pt.payload.get("title"),
                        "domain": pt.payload.get("domain"),
                        "snippet": pt.payload.get("content"),
                        "relevance_score": round(float(pt.score), 4),
                    }
                )

        if not matches:
            # Fallback keyword match across in-memory docs
            query_lower = query.lower()
            for doc in KNOWLEDGE_DOCS:
                if any(w in doc["content"].lower() or w in doc["title"].lower() for w in query_lower.split()):
                    matches.append(
                        {
                            "title": doc["title"],
                            "domain": doc["domain"],
                            "snippet": doc["content"],
                            "relevance_score": 0.85,
                        }
                    )

        return {
            "query": query,
            "domain_filter": domain_filter,
            "match_count": len(matches),
            "results": matches[:limit],
        }

    def as_registered_tool(self) -> RegisteredTool:
        """Expose RAG tool as a RegisteredTool so the LLM reasoning node can bind it."""
        return RegisteredTool(
            name="rag_pipeline_tool",
            domain="general",
            method="GET",
            path="/system/rag",
            description=(
                "Retrieval-Augmented Generation Tool: Queries developer documentation, error codes, "
                "ISO currencies, and API guides. Call this when you need knowledge, validation rules, "
                "or error code explanations."
            ),
            keywords=["documentation", "guide", "error code", "rules", "refund reason", "rag"],
            parameters=[
                ParameterSchema(
                    name="query",
                    type="string",
                    description="The search question or topic, e.g. 'refund reason codes' or '422 error'",
                    required=True,
                    location="query",
                ),
                ParameterSchema(
                    name="domain_filter",
                    type="string",
                    description="Optional filter: 'invoicing', 'payments', 'disputes', 'reporting'",
                    required=False,
                    location="query",
                ),
            ],
            is_mutating=False,
            source="meta_tool",
        )


_rag_tool_instance: Optional[RAGPipelineTool] = None


def get_rag_tool() -> RAGPipelineTool:
    global _rag_tool_instance
    if _rag_tool_instance is None:
        _rag_tool_instance = RAGPipelineTool()
    return _rag_tool_instance

