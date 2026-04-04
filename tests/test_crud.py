"""Tests for source CRUD and wiki endpoints."""
import json
import uuid
import pytest

from app.database import get_db


async def _insert_source(source_id=None, title="Test Source", source_type="article", url=None):
    """Helper to insert a source."""
    sid = source_id or str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sources (id, url, source_type, title, content_hash, raw_content, enrichment_status, ingested_at, summary, user_context) VALUES (?, ?, ?, ?, 'hash123', 'Raw content.', 'complete', datetime('now'), 'A summary.', NULL)",
            (sid, url or f"https://example.com/{sid}", source_type, title),
        )
        await db.commit()
    return sid


async def _insert_entity(source_id, entity_type="person", entity_name="John Doe"):
    """Helper to insert an entity."""
    eid = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO entities (id, source_id, entity_type, entity_name) VALUES (?, ?, ?, ?)",
            (eid, source_id, entity_type, entity_name),
        )
        await db.commit()
    return eid


async def _insert_wiki_page(slug, title, content=None, stale=0, source_count=0):
    """Helper to insert a wiki page."""
    page_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO wiki_pages (id, slug, title, content, stale, source_count, last_compiled_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            (page_id, slug, title, content, stale, source_count),
        )
        await db.commit()
    return page_id


async def _link_source_to_page(page_id, source_id):
    """Helper to link a source to a wiki page."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO wiki_source_links (wiki_page_id, source_id) VALUES (?, ?)",
            (page_id, source_id),
        )
        await db.commit()


# --- Source CRUD Tests ---

@pytest.mark.asyncio
async def test_list_sources_empty(client):
    """GET /sources → empty list."""
    resp = await client.get("/sources")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_sources_with_data(client):
    """Insert sources, GET /sources → correct count."""
    await _insert_source(title="Source A")
    await _insert_source(title="Source B")

    resp = await client.get("/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_list_sources_filter_type(client):
    """GET /sources?type=article → filtered."""
    await _insert_source(title="Article", source_type="article")
    await _insert_source(title="Video", source_type="video")

    resp = await client.get("/sources?type=article")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source_type"] == "article"


@pytest.mark.asyncio
async def test_get_source_detail(client):
    """GET /sources/{id} → full detail + entities."""
    sid = await _insert_source(title="Detail Source")
    await _insert_entity(sid, "person", "Alice")
    await _insert_entity(sid, "company", "Acme Corp")

    resp = await client.get(f"/sources/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sid
    assert data["title"] == "Detail Source"
    assert len(data["entities"]) == 2
    entity_names = {e["name"] for e in data["entities"]}
    assert "Alice" in entity_names
    assert "Acme Corp" in entity_names


@pytest.mark.asyncio
async def test_get_source_not_found(client):
    """GET /sources/bad-id → 404."""
    resp = await client.get("/sources/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_source(client):
    """PATCH user_context → updated."""
    sid = await _insert_source(title="Patch Me")

    resp = await client.patch(f"/sources/{sid}", json={"user_context": "Updated context"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sid
    assert "user_context" in data["updated"]

    # Verify in DB
    async with get_db() as db:
        cursor = await db.execute("SELECT user_context FROM sources WHERE id = ?", (sid,))
        row = await cursor.fetchone()
    assert row[0] == "Updated context"


@pytest.mark.asyncio
async def test_patch_source_invalid_field(client):
    """PATCH with forbidden field → 422."""
    sid = await _insert_source(title="No Touch")

    resp = await client.patch(f"/sources/{sid}", json={"raw_content": "hacked"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_source(client):
    """DELETE → cascade delete verified."""
    sid = await _insert_source(title="Delete Me")
    await _insert_entity(sid, "person", "Bob")

    # Also add a chunk
    async with get_db() as db:
        await db.execute(
            "INSERT INTO chunks (id, source_id, chunk_index, content) VALUES (?, ?, 0, 'chunk text')",
            (str(uuid.uuid4()), sid),
        )
        await db.commit()

    resp = await client.delete(f"/sources/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] is True

    # Verify cascade — source gone
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM sources WHERE id = ?", (sid,))
        assert await cursor.fetchone() is None

        # Entities gone
        cursor = await db.execute("SELECT id FROM entities WHERE source_id = ?", (sid,))
        assert await cursor.fetchone() is None

        # Chunks gone
        cursor = await db.execute("SELECT id FROM chunks WHERE source_id = ?", (sid,))
        assert await cursor.fetchone() is None


@pytest.mark.asyncio
async def test_delete_source_not_found(client):
    """DELETE nonexistent → 404."""
    resp = await client.delete("/sources/nonexistent-id")
    assert resp.status_code == 404


# --- Wiki Endpoint Tests ---

@pytest.mark.asyncio
async def test_list_wiki_pages(client):
    """GET /wiki → list."""
    await _insert_wiki_page("topic-a", "Topic A", source_count=3)
    await _insert_wiki_page("topic-b", "Topic B", stale=1)

    resp = await client.get("/wiki")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    slugs = {p["slug"] for p in data}
    assert "topic-a" in slugs
    assert "topic-b" in slugs


@pytest.mark.asyncio
async def test_get_wiki_page(client):
    """GET /wiki/{slug} → full page + linked sources."""
    page_id = await _insert_wiki_page("my-topic", "My Topic", content="# My Topic\nContent here.", source_count=1)
    sid = await _insert_source(title="Linked Source")
    await _link_source_to_page(page_id, sid)

    resp = await client.get("/wiki/my-topic")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "my-topic"
    assert data["title"] == "My Topic"
    assert data["content"] == "# My Topic\nContent here."
    assert len(data["sources"]) == 1
    assert data["sources"][0]["title"] == "Linked Source"


@pytest.mark.asyncio
async def test_get_wiki_page_not_found(client):
    """GET /wiki/nonexistent → 404."""
    resp = await client.get("/wiki/nonexistent")
    assert resp.status_code == 404
