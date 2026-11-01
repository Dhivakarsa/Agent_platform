"""
Email communication tools for draft generation and high-risk message dispatch.
Splits drafting (reversible, no approval) and sending (external write, requires HITL approval).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
import uuid

# In-memory drafts storage
_DRAFTS_STORE: Dict[str, Dict[str, Any]] = {}
_SENT_STORE: List[Dict[str, Any]] = []


def create_email_draft(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create and save an email draft. Safe reversible action, does not require approval.
    """
    draft_id = f"draft_{uuid.uuid4().hex[:8]}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "body": body,
        "cc": cc or "",
        "created_at": created_at,
        "status": "DRAFT",
    }
    _DRAFTS_STORE[draft_id] = record
    return {
        "status": "success",
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "message": f"Draft {draft_id} created successfully. Ready for review and sending.",
    }


def send_email(
    to: str,
    subject: str,
    body: str,
    draft_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dispatch an outbound email. High-risk EXTERNAL_WRITE action that requires HITL approval.
    Sends real email via SMTP if SMTP_USER and SMTP_PASSWORD are configured in .env,
    otherwise records locally in simulation mode.
    """
    import os
    sent_id = f"msg_{uuid.uuid4().hex[:8]}"
    dispatched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    from app.config import get_settings
    settings = get_settings()

    smtp_user = (settings.smtp_user or os.environ.get("SMTP_USER", "")).strip()
    smtp_pass = (settings.smtp_password or os.environ.get("SMTP_PASSWORD", "")).strip()
    smtp_host = (settings.smtp_host or os.environ.get("SMTP_HOST", "smtp.gmail.com")).strip()
    smtp_port = int(settings.smtp_port or os.environ.get("SMTP_PORT", 587))
    smtp_from = (settings.smtp_from or os.environ.get("SMTP_FROM", smtp_user)).strip()

    real_sent = False
    status_note = "Dispatched in local simulation mode."

    if smtp_user and smtp_pass:
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = smtp_from or smtp_user
            msg["To"] = to
            msg.set_content(body)

            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

            real_sent = True
            status_note = f"Real email dispatched via SMTP to {to}."
        except Exception as exc:
            status_note = f"SMTP dispatch error ({exc}); saved in audit store."

    record = {
        "message_id": sent_id,
        "to": to,
        "subject": subject,
        "body": body,
        "dispatched_at": dispatched_at,
        "status": "DELIVERED" if real_sent else "SIMULATED",
        "note": status_note,
    }
    _SENT_STORE.append(record)

    if draft_id and draft_id in _DRAFTS_STORE:
        _DRAFTS_STORE[draft_id]["status"] = "SENT"

    return {
        "status": "success",
        "message_id": sent_id,
        "to": to,
        "subject": subject,
        "dispatched_at": dispatched_at,
        "real_smtp_delivery": real_sent,
        "message": status_note,
    }


def list_email_drafts() -> Dict[str, Any]:
    """Retrieve all active email drafts."""
    return {
        "status": "success",
        "count": len(_DRAFTS_STORE),
        "drafts": list(_DRAFTS_STORE.values()),
    }

