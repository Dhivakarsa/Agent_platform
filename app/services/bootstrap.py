"""
One-time startup bootstrap: database schema and the seeded tool catalog.

Lives here rather than in ``app.main`` so the graph can ensure the platform is ready
without importing the FastAPI application, which was a circular import. It also tracks
whether the work has already been done, so entering through the agent rather than through
the API does not re-run schema creation on every turn.
"""

from __future__ import annotations

import logging

from app.db.session import init_db
from app.meta_tools.rag_pipeline_tool import get_rag_tool
from app.services.document_service import get_document_service
from app.services.ingestion import PostmanCollectionIngester, generate_full_paypal_collection
from app.services.tool_registry import get_tool_registry

logger = logging.getLogger(__name__)

_db_ready = False


async def seed_system_catalog() -> int:
    """
    Populate the catalog if it is empty, and return the number of registered tools.

    Cheap to call repeatedly: it returns the current count once the catalog is populated.
    """
    registry = get_tool_registry()
    if registry.get_tool_count() > 0:
        return registry.get_tool_count()

    logger.info("Auto-seeding system catalog with the PayPal collection...")
    collection_data = generate_full_paypal_collection()
    ingester = PostmanCollectionIngester(collection_data=collection_data)
    tools = ingester.parse_collection()
    await registry.register_batch(tools)
    await get_document_service().seed_default_sources()

    # Register the two pinned meta-tools so retrieval and the planner can select them.
    try:
        from app.meta_tools.system_search_tool import get_system_search_tool

        await registry.register_batch([
            get_rag_tool().as_registered_tool(),
            get_system_search_tool().as_registered_tool(),
        ])
        logger.info("Registered pinned meta-tools (RAG Pipeline, System Search).")
    except Exception as meta_err:
        logger.warning("Meta-tool registration failed: %s", meta_err)

    # General-purpose calculation, time, and communication tools.
    try:
        from app.meta_tools.calculator_tool import get_general_tool_definitions

        gen_tools = get_general_tool_definitions()
        await registry.register_batch(gen_tools)
        logger.info("Auto-seeded %d general-purpose tools.", len(gen_tools))
    except Exception as gen_err:
        logger.warning("General tools auto-seed failed: %s", gen_err)

    # Built-in MCP tools (Fetch & Filesystem).
    try:
        from app.services.mcp_client import get_mcp_service

        mcp_svc = get_mcp_service()
        await mcp_svc.list_servers()
        await mcp_svc.register_mcp_tools("fetch")
        await mcp_svc.register_mcp_tools("filesystem")
        logger.info("Auto-seeded MCP tools (Fetch & Filesystem).")
    except Exception as mcp_err:
        logger.warning("MCP auto-seed skipped: %s", mcp_err)

    logger.info("Catalog auto-seed completed with %d tools.", len(tools))
    return len(tools)


async def ensure_platform_ready() -> None:
    """
    Make sure the schema exists and the catalog is seeded, doing the work only once.

    The API does this during startup. The agent calls it too, so that running a turn
    directly - from a test or a script - works without the application lifespan, but
    without paying for schema creation on every turn.
    """
    global _db_ready
    if not _db_ready:
        await init_db()
        _db_ready = True
    await seed_system_catalog()


def reset_bootstrap_state() -> None:
    """Forget that initialisation happened, so a test can point at a fresh database."""
    global _db_ready
    _db_ready = False
