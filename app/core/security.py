"""
Security module: Password hashing, JWT token management, and RBAC FastAPI dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)
security_bearer = HTTPBearer(auto_error=False)


# ── Password Hashing (PBKDF2-HMAC-SHA256) ────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash password securely using PBKDF2-HMAC-SHA256 with random salt."""
    salt = os.urandom(16)
    iterations = 100_000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    derived_b64 = base64.b64encode(derived).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${derived_b64}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against the stored salted hash."""
    try:
        parts = hashed.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2].encode("ascii"))
        expected_hash = base64.b64decode(parts[3].encode("ascii"))
        actual_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual_hash, expected_hash)
    except Exception as exc:
        logger.warning("Error verifying password: %s", exc)
        return False


# ── JWT Token Implementation ────────────────────────────────────────────────


def create_access_token(data: dict[str, Any], expires_delta_seconds: Optional[int] = None) -> str:
    """Create a signed JWT token using HMAC-SHA256."""
    settings = get_settings()
    now = int(time.time())
    expire = now + (expires_delta_seconds or (settings.access_token_expire_minutes * 60))

    payload = dict(data)
    payload["iat"] = now
    payload["exp"] = expire

    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).decode("utf-8").rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(settings.jwt_secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode a signed JWT token."""
    settings = get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")

    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(settings.jwt_secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()

    # Add padding back
    rem = len(sig_b64) % 4
    if rem:
        sig_b64 += "=" * (4 - rem)
    try:
        actual_sig = base64.urlsafe_b64decode(sig_b64.encode("utf-8"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signature mismatch")

    # Decode payload
    rem_p = len(payload_b64) % 4
    if rem_p:
        payload_b64 += "=" * (4 - rem_p)
    payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
    payload = json.loads(payload_bytes.decode("utf-8"))

    if payload.get("exp", 0) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")

    return payload


# ── Bootstrap Accounts Seeder ────────────────────────────────────────────────


async def seed_bootstrap_accounts() -> None:
    """
    Idempotently seeds the 2 initial demo accounts from .env if they do not exist:
    - Admin: BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD
    - Normal User: BOOTSTRAP_USER_EMAIL / BOOTSTRAP_USER_PASSWORD
    Does NOT overwrite password if the user already exists.
    """
    settings = get_settings()
    async with async_session_factory() as s:
        accounts = [
            (settings.bootstrap_admin_email, settings.bootstrap_admin_password, "admin"),
            (settings.bootstrap_user_email, settings.bootstrap_user_password, "user"),
        ]
        for email, password, role in accounts:
            stmt = select(User).where(User.email == email)
            res = await s.execute(stmt)
            existing = res.scalar_one_or_none()
            if not existing:
                hashed = hash_password(password)
                user = User(
                    email=email,
                    password_hash=hashed,
                    role=role,
                    is_active=True,
                )
                s.add(user)
                logger.info("Bootstrapped local account '%s' with role '%s' into database.", email, role)
        await s.commit()


# ── FastAPI Dependencies ─────────────────────────────────────────────────────


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> dict[str, Any]:
    """
    FastAPI dependency: Authenticates request and yields verified user context.
    Raises 401 Unauthorized if missing or invalid.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Please login.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    email = payload.get("email")
    role = payload.get("role", "user")

    if not user_id or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token payload")

    # Verify user exists and is active in database
    async with async_session_factory() as s:
        stmt = select(User).where(User.user_id == user_id)
        res = await s.execute(stmt)
        user = res.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")

    return {
        "user_id": user_id,
        "email": email,
        "role": role,
    }


async def require_admin(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """
    FastAPI dependency: Enforces that the current authenticated user has the 'admin' role.
    Raises 403 Forbidden otherwise.
    """
    if current_user.get("role") != "admin":
        logger.warning("Access denied: User '%s' lacks admin role.", current_user.get("email"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator permissions required for this operation.",
        )
    return current_user
