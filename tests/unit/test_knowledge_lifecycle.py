"""
Tests for knowledge-base indexing, retrieval and deletion.

Covers the two correctness properties that make RAG trustworthy: a deleted document stops
being retrievable, and the provider that indexed a chunk is the provider that embeds the
query (otherwise the vectors are in different spaces and search returns noise).
"""

from __future__ import annotations

import pytest

from app.db.session import init_db
from app.meta_tools.rag_pipeline_tool import get_rag_tool
from app.services.document_service import get_document_service
from app.services.embedding_service import EmbeddingService, get_embedding_service


# ── Shared provider ──────────────────────────────────────────────────────────


def test_tool_index_and_knowledge_base_share_one_provider():
    """A single provider instance keeps every collection in the same vector space."""
    from app.services.tool_registry import get_tool_registry

    shared = get_embedding_service()
    assert get_document_service().embeddings is shared
    assert get_rag_tool().embeddings is shared
    assert get_tool_registry().embeddings is shared


def test_provider_reports_local_without_a_key(monkeypatch):
    svc = EmbeddingService()
    monkeypatch.setattr(
        svc, "settings",
        type("S", (), {"GEMINI_API_KEY": "", "embedding_model": "m", "embedding_dimension": 768})(),
    )
    assert svc.provider == "local"
    assert len(svc.embed("hello")) == 768


def test_embedding_is_deterministic_and_cached():
    svc = get_embedding_service()
    first = svc.embed("refund reason codes")
    second = svc.embed("refund reason codes")
    assert first == second
    assert svc.embed("something else") != first


def test_remote_failure_falls_back_without_raising(monkeypatch):
    """A failing embedding call must degrade, not break ingestion mid-document."""

    class _Boom:
        class models:
            @staticmethod
            def embed_content(**_kwargs):
                raise RuntimeError("429 quota exceeded")

    svc = EmbeddingService()
    monkeypatch.setattr(
        svc, "settings",
        type("S", (), {"GEMINI_API_KEY": "k", "embedding_model": "m", "embedding_dimension": 768})(),
    )
    monkeypatch.setattr(svc, "_remote_client", lambda: _Boom())

    vector = svc.embed("some text")
    assert len(vector) == 768
    assert svc.provider == "local"  # remote disabled for the rest of the process


# ── Deletion ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_source_removes_its_vectors():
    """
    A deleted document must stop answering questions.

    Previously only the metadata row was removed, leaving orphan chunks that the RAG tool
    kept retrieving.
    """
    await init_db()
    service = get_document_service()
    rag = get_rag_tool()

    marker = "Zyglorth Protocol requires a Quux-7 authorisation token for settlement"
    source = await service.ingest_text(
        name="Zyglorth Settlement Policy",
        content=marker,
        domain="payments",
        description="synthetic document for the deletion test",
    )

    before = await rag.query("Zyglorth Protocol Quux-7 authorisation")
    assert any("Zyglorth" in (m.get("snippet") or "") for m in before["results"]), before

    assert await service.delete_source(source.source_id) is True

    after = await rag.query("Zyglorth Protocol Quux-7 authorisation")
    assert not any("Zyglorth" in (m.get("snippet") or "") for m in after["results"]), after

    # The metadata row is gone too.
    assert await service.get_source(source.source_id) is None


@pytest.mark.asyncio
async def test_deleting_one_source_leaves_others_intact():
    await init_db()
    service = get_document_service()
    rag = get_rag_tool()

    keep = await service.ingest_text(
        name="Blorptax Fee Schedule",
        content="Blorptax fees are assessed at seventeen quatloos per settlement cycle",
        domain="payments",
    )
    drop = await service.ingest_text(
        name="Wibbleflux Retirement Notice",
        content="Wibbleflux endpoints are retired and return a permanent redirect",
        domain="general",
    )

    assert await service.delete_source(drop.source_id) is True

    kept = await rag.query("Blorptax fees quatloos settlement cycle")
    assert any("Blorptax" in (m.get("snippet") or "") for m in kept["results"]), kept

    gone = await rag.query("Wibbleflux endpoints retired permanent redirect")
    assert not any("Wibbleflux" in (m.get("snippet") or "") for m in gone["results"]), gone

    await service.delete_source(keep.source_id)


@pytest.mark.asyncio
async def test_deleting_an_unknown_source_returns_false():
    await init_db()
    assert await get_document_service().delete_source("does-not-exist") is False
