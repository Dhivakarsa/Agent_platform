"""
Async SQLAlchemy engine and session factory.

The module exposes:
* ``async_engine``  – the shared ``AsyncEngine`` instance.
* ``async_session_factory`` – a session-maker bound to that engine.
* ``get_session``   – an async-generator suitable for FastAPI ``Depends()``.
* ``init_db``       – creates all ORM tables and bootstraps initial accounts.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_connect_args: dict = {}
if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

async_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    connect_args=_connect_args,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an ``AsyncSession`` and guarantee cleanup."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Table creation helper (local dev / SQLite convenience)
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create all ORM tables and bootstrap initial local demo accounts."""
    from app.db.models import Base
    from app.core.security import seed_bootstrap_accounts
    from sqlalchemy import text

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Auto-migrate SQLite schema if new columns were added to existing DB
        if settings.DATABASE_URL.startswith("sqlite"):
            def _migrate_sqlite_columns(connection):
                try:
                    res = connection.execute(text("PRAGMA table_info(tool_definitions)"))
                    existing_cols = {row[1] for row in res.fetchall()}
                    if existing_cols:
                        if "source_type" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN source_type VARCHAR(32) DEFAULT 'http_api'"))
                        if "mcp_server_name" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN mcp_server_name VARCHAR(128)"))
                        if "remote_tool_name" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN remote_tool_name VARCHAR(128)"))
                        if "risk_level" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN risk_level VARCHAR(32) DEFAULT 'read_only'"))
                        if "approval_policy" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN approval_policy VARCHAR(64) DEFAULT 'standard'"))
                        if "idempotency" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN idempotency VARCHAR(64) DEFAULT 'standard'"))
                        if "namespace" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN namespace VARCHAR(128) DEFAULT 'paypal'"))
                        if "capabilities" not in existing_cols:
                            connection.execute(text("ALTER TABLE tool_definitions ADD COLUMN capabilities JSON DEFAULT '[]'"))

                    res = connection.execute(text("PRAGMA table_info(agent_runs)"))
                    run_cols = {row[1] for row in res.fetchall()}
                    if run_cols and "final_response" not in run_cols:
                        connection.execute(text("ALTER TABLE agent_runs ADD COLUMN final_response TEXT"))
                except Exception:
                    pass

            await conn.run_sync(_migrate_sqlite_columns)

    # Seed the two demo accounts (Admin & User)
    await seed_bootstrap_accounts()
