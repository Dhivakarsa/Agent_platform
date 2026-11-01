"""
Typed Pydantic input schemas for key PayPal API operations.

Each schema maps to one agent-callable PayPal tool, enforcing required fields
and providing sensible defaults so the LLM can populate them from
natural-language instructions.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Shared enums / sub-models
# =============================================================================


class CurrencyCode(StrEnum):
    """Subset of ISO 4217 currency codes commonly used with PayPal."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    AUD = "AUD"
    CAD = "CAD"
    JPY = "JPY"


class Money(BaseModel):
    """Monetary amount with currency."""

    currency_code: CurrencyCode = Field(default=CurrencyCode.USD, examples=["USD"])
    value: str = Field(..., examples=["49.99"], description="Decimal string amount.")


class OrderIntent(StrEnum):
    """PayPal order intent."""

    CAPTURE = "CAPTURE"
    AUTHORIZE = "AUTHORIZE"


class Item(BaseModel):
    """Line item in an order or invoice."""

    name: str = Field(..., max_length=127, examples=["Widget"])
    quantity: str = Field(default="1", examples=["2"])
    unit_amount: Money = Field(...)
    description: str | None = Field(default=None, max_length=127)
    sku: str | None = Field(default=None, max_length=127)


class ShippingAddress(BaseModel):
    """Postal address for shipping."""

    address_line_1: str = Field(..., max_length=300)
    address_line_2: str | None = Field(default=None, max_length=300)
    admin_area_2: str = Field(..., max_length=120, description="City.")
    admin_area_1: str | None = Field(default=None, max_length=300, description="State / province.")
    postal_code: str = Field(..., max_length=60)
    country_code: str = Field(default="US", min_length=2, max_length=2)


# =============================================================================
# Orders
# =============================================================================


class PurchaseUnit(BaseModel):
    """One purchase unit within a create-order request."""

    reference_id: str | None = Field(default=None, max_length=256)
    amount: Money = Field(...)
    items: list[Item] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=127)
    shipping: ShippingAddress | None = Field(default=None)


class CreateOrderRequest(BaseModel):
    """Input schema for ``create_order`` tool.

    Maps to ``POST /v2/checkout/orders``.
    """

    intent: OrderIntent = Field(default=OrderIntent.CAPTURE)
    purchase_units: list[PurchaseUnit] = Field(
        ...,
        min_length=1,
        description="At least one purchase unit is required.",
    )
    return_url: str | None = Field(default=None, description="Redirect URL after approval.")
    cancel_url: str | None = Field(default=None, description="Redirect URL on cancellation.")


class CaptureOrderRequest(BaseModel):
    """Input schema for ``capture_order`` tool.

    Maps to ``POST /v2/checkout/orders/{order_id}/capture``.
    """

    order_id: str = Field(
        ...,
        min_length=1,
        description="PayPal order ID to capture.",
        examples=["5O190127TN364715T"],
    )


# =============================================================================
# Invoices
# =============================================================================


class RecipientInfo(BaseModel):
    """Invoice recipient."""

    email_address: str = Field(..., description="Recipient email address.")
    given_name: str | None = Field(default=None, max_length=140)
    surname: str | None = Field(default=None, max_length=140)


class InvoiceItem(BaseModel):
    """Line item on an invoice."""

    name: str = Field(..., max_length=200)
    quantity: str = Field(default="1")
    unit_amount: Money = Field(...)
    description: str | None = Field(default=None, max_length=1000)


class CreateInvoiceRequest(BaseModel):
    """Input schema for ``create_invoice`` tool.

    Maps to ``POST /v2/invoicing/invoices``.
    """

    recipient: RecipientInfo = Field(...)
    items: list[InvoiceItem] = Field(..., min_length=1)
    note: str | None = Field(default=None, max_length=4000)
    terms_and_conditions: str | None = Field(default=None, max_length=4000)
    due_date: date | None = Field(default=None, description="Invoice due date (YYYY-MM-DD).")
    currency_code: CurrencyCode = Field(default=CurrencyCode.USD)


class SendInvoiceRequest(BaseModel):
    """Input schema for ``send_invoice`` tool.

    Maps to ``POST /v2/invoicing/invoices/{invoice_id}/send``.
    """

    invoice_id: str = Field(
        ...,
        min_length=1,
        description="PayPal invoice ID to send.",
        examples=["INV2-Z56S-5LLA-Q52L-CPZ5"],
    )
    subject: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4000)
    send_to_recipient: bool = Field(
        default=True,
        description="Whether PayPal should email the invoice to the recipient.",
    )


# =============================================================================
# Payouts
# =============================================================================


class PayoutRecipient(BaseModel):
    """A single recipient in a batch payout."""

    recipient_type: str = Field(default="EMAIL", description="EMAIL | PHONE | PAYPAL_ID")
    amount: Money = Field(...)
    receiver: str = Field(..., description="Email, phone, or PayPal ID of the recipient.")
    note: str | None = Field(default=None, max_length=4000)
    sender_item_id: str | None = Field(default=None, max_length=256)


class CreatePayoutRequest(BaseModel):
    """Input schema for ``create_payout`` tool.

    Maps to ``POST /v1/payments/payouts``.
    """

    sender_batch_header: dict[str, Any] = Field(
        default_factory=lambda: {"email_subject": "You have a payout!"},
        description="Batch header with email_subject, sender_batch_id, etc.",
    )
    items: list[PayoutRecipient] = Field(
        ...,
        min_length=1,
        description="One or more payout recipients.",
    )


# =============================================================================
# Disputes
# =============================================================================


class DisputeStatus(StrEnum):
    """Possible dispute statuses for filtering."""

    OPEN = "OPEN"
    WAITING_FOR_BUYER_RESPONSE = "WAITING_FOR_BUYER_RESPONSE"
    WAITING_FOR_SELLER_RESPONSE = "WAITING_FOR_SELLER_RESPONSE"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    OTHER = "OTHER"


class ListDisputesRequest(BaseModel):
    """Input schema for ``list_disputes`` tool.

    Maps to ``GET /v1/customer/disputes``.
    """

    status: DisputeStatus | None = Field(
        default=None,
        description="Filter disputes by status.",
    )
    page_size: int = Field(default=10, ge=1, le=50)
    start_time: str | None = Field(
        default=None,
        description="ISO-8601 start date filter.",
        examples=["2024-01-01T00:00:00Z"],
    )
    next_page_token: str | None = Field(
        default=None,
        description="Token for cursor-based pagination.",
    )


# =============================================================================
# Transactions
# =============================================================================


class SearchTransactionsRequest(BaseModel):
    """Input schema for ``search_transactions`` tool.

    Maps to ``GET /v1/reporting/transactions``.
    """

    start_date: str = Field(
        ...,
        description="ISO-8601 start datetime.",
        examples=["2024-01-01T00:00:00-0700"],
    )
    end_date: str = Field(
        ...,
        description="ISO-8601 end datetime.",
        examples=["2024-01-31T23:59:59-0700"],
    )
    transaction_id: str | None = Field(default=None, description="Specific transaction ID.")
    transaction_status: str | None = Field(
        default=None,
        description="Filter by status (e.g. S for Success, D for Denied).",
    )
    page_size: int = Field(default=100, ge=1, le=500)
    page: int = Field(default=1, ge=1)
    fields: str = Field(
        default="all",
        description="Comma-separated field list or 'all'.",
    )
