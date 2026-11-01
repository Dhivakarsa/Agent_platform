"""
Catalog generator for scalability benchmarks (50, 100, 500, 1,000+ tools).
Combines real 50+ PayPal endpoints with synthetic enterprise service tools.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.schemas.tool import RegisteredTool
from app.services.ingestion import PostmanCollectionIngester, create_synthetic_tools, generate_full_paypal_collection

logger = logging.getLogger(__name__)


def generate_benchmark_catalog(target_size: int = 50) -> list[RegisteredTool]:
    """
    Generate a catalog of exact target size.
    Always includes the complete 50+ PayPal endpoints first, then supplements with synthetic tools.
    """
    collection = generate_full_paypal_collection()
    ingester = PostmanCollectionIngester(collection_data=collection)
    paypal_tools = ingester.parse_collection()

    if target_size <= len(paypal_tools):
        return paypal_tools[:target_size]

    remaining = target_size - len(paypal_tools)
    synthetic = create_synthetic_tools(count=remaining)
    full_catalog = paypal_tools + synthetic
    logger.info("Generated benchmark catalog of size %d (%d PayPal, %d Synthetic)", len(full_catalog), len(paypal_tools), len(synthetic))
    return full_catalog

