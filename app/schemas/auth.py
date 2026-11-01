"""
Pydantic schemas for authentication and user sessions.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials payload for /auth/login."""
    email: str = Field(..., description="User email address", examples=["admin@examplemail.com"])
    password: str = Field(..., min_length=1, description="Plaintext password", examples=["admin@123"])


class UserResponse(BaseModel):
    """Public user identity details."""
    user_id: str
    email: str
    role: str
    is_active: bool = True

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Bearer token payload returned upon successful login."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 28800
    user: UserResponse
