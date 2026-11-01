"""
Pydantic schemas for knowledge sources, document uploads, and URL ingestion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, HttpUrl


class SourceCreateURLRequest(BaseModel):
    """Payload to ingest a remote documentation URL."""
    name: str = Field(..., min_length=2, max_length=255, description="Document display title")
    url: str = Field(..., description="Target web page or documentation link")
    domain: str = Field(default="general", description="Subject domain: invoicing, payments, disputes, etc.")
    description: str = Field(default="", description="Summary of the documentation topic")


class SourceCreateTextRequest(BaseModel):
    """Payload to add raw text or API reference documentation."""
    name: str = Field(..., min_length=2, max_length=255)
    domain: str = Field(default="general")
    description: str = Field(default="")
    content: str = Field(..., min_length=10, description="Full markdown or text content")


class SourceResponse(BaseModel):
    """Public details of an indexed knowledge source."""
    source_id: str
    name: str
    source_type: str
    domain: str
    description: str
    url: Optional[str] = None
    status: str
    chunk_count: int
    version: str
    created_at: datetime

    model_config = {"from_attributes": True}

