"""
Unit tests for Role-Based Access Control (RBAC) and user isolation.
"""

import pytest
from fastapi import HTTPException
from app.core.security import require_admin


@pytest.mark.asyncio
async def test_admin_permission_allowed():
    admin_user = {"user_id": "u-admin", "email": "admin@examplemail.com", "role": "admin"}
    res = await require_admin(admin_user)
    assert res["role"] == "admin"


@pytest.mark.asyncio
async def test_normal_user_rejected_from_admin_action():
    normal_user = {"user_id": "u-user", "email": "user@examplemail.com", "role": "user"}
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(normal_user)

    assert exc_info.value.status_code == 403
    assert "Administrator permissions required" in exc_info.value.detail

