"""Tests for Step 1: scaffold, schema, health, and stats endpoints."""
import pytest
import aiosqlite
from pathlib import Path


pytestmark = pytest.mark.asyncio


async def test_health_returns_200(client):
    """GET /health returns 200 with status='ok'."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "db_size_mb" in data
    assert "uptime_seconds" in data
    assert isinstance(data["db_size_mb"], float)
    assert isinstance(data["uptime_seconds"], float)


async def test_stats_returns_zeroes(client):
    """GET /stats returns 200 with all zeroes for empty DB."""
    resp = await client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_sources"] == 0
    assert data["total_chunks"] == 0
    assert data["total_wiki_pages"] == 0
    assert data["stale_topics"] == 0
    assert data["by_type"] == {}


async def test_tables_exist(setup_temp_db):
    """Verify all 6 tables exist with correct columns."""
    db_path = setup_temp_db

    expected_tables = {
        "sources": [
            "id", "url", "source_type", "title", "author", "published_at",
            "ingested_at", "content_hash", "raw_content", "metadata",
            "user_context", "attribution", "summary", "key_insights",
            "topics", "enrichment_status",
        ],
        "chunks": ["id", "source_id", "chunk_index", "content", "embedding", "token_count"],
        "entities": ["id", "source_id", "entity_type", "entity_name"],
        "enrichment_jobs": [
            "id", "source_id", "stage", "status", "attempt",
            "result", "error", "created_at", "completed_at",
        ],
        "wiki_pages": [
            "id", "slug", "title", "content", "source_count",
            "embedding", "stale", "created_at", "last_compiled_at",
        ],
        "wiki_source_links": ["wiki_page_id", "source_id"],
    }

    async with aiosqlite.connect(db_path) as db:
        # Check each table exists and has correct columns
        for table_name, expected_cols in expected_tables.items():
            cursor = await db.execute(f"PRAGMA table_info({table_name})")
            rows = await cursor.fetchall()
            actual_cols = [row[1] for row in rows]
            assert len(actual_cols) > 0, f"Table {table_name} does not exist"
            for col in expected_cols:
                assert col in actual_cols, f"Column {col} missing from {table_name}"


async def test_404_error_format(client):
    """GET /nonexistent returns 404 with standard error format."""
    resp = await client.get("/nonexistent")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert "detail" in data
