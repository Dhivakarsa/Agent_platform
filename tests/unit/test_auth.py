"""
Unit tests for password hashing, token generation, and authentication verification.
"""

import pytest
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hashing():
    password = "admin@123"
    hashed = hash_password(password)

    # Must be salted PBKDF2 hash, not plaintext
    assert hashed.startswith("pbkdf2_sha256$")
    assert password not in hashed

    # Valid verification
    assert verify_password(password, hashed) is True

    # Invalid password rejection
    assert verify_password("wrong_password", hashed) is False


def test_jwt_token_lifecycle():
    user_payload = {
        "sub": "user-uuid-12345",
        "email": "user@examplemail.com",
        "role": "user",
    }
    token = create_access_token(user_payload, expires_delta_seconds=3600)

    assert isinstance(token, str)
    assert len(token.split(".")) == 3

    decoded = decode_access_token(token)
    assert decoded["sub"] == "user-uuid-12345"
    assert decoded["email"] == "user@examplemail.com"
    assert decoded["role"] == "user"

