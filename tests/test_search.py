"""Tests for vector search, wiki search, and query synthesis."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.database import get_db
from app.embedding import serialize_embedding


# --- Helpers ---

def make_embedding(seed: int, dims: int = 10) -> list[float]:
    """Create a deterministic normalized embedding for testing."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(dims)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


async def insert_source(db, source_id: str, title: str = "Test Source", url: str = None,
                        ingested_at: str = None):
    """Insert a source row for testing."""
    if url is None:
        url = f"https://example.com/{source_id}"
    if ingested_at is None:
        ingested_at = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO sources (id, url, source_type, title, ingested_at, content_hash) VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, url, "article", title, ingested_at, f"hash-{source_id}"),
    )
    await db.commit()


async def insert_chunk(db, chunk_id: str, source_id: str, content: str, embedding: list[float],
                       chunk_index: int = 0):
    """Insert a chunk with a serialized embedding."""
    await db.execute(
        "INSERT INTO chunks (id, source_id, chunk_index, content, embedding) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, source_id, chunk_index, content, serialize_embedding(embedding)),
    )
    await db.commit()


async def insert_wiki_page(db, slug: str, title: str, content: str, embedding: list[float]):
    """Insert a wiki page with a serialized embedding."""
    page_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO wiki_pages (id, slug, title, content, embedding) VALUES (?, ?, ?, ?, ?)",
        (page_id, slug, title, content, serialize_embedding(embedding)),
    )
    await db.commit()


# --- Vector Search Tests ---

@pytest.mark.asyncio
async def test_vector_search_empty_db(setup_temp_db):
    """Query against an empty DB should return an empty list."""
    query_emb = make_embedding(0)
    with patch("app.search.embed_text", new_callable=AsyncMock, return_value=query_emb):
        from app.search import vector_search
        results = await vector_search("anything")
    assert results == []


@pytest.mark.asyncio
async def test_vector_search_returns_ranked(setup_temp_db):
    """Results should be ranked by cosine similarity (with time boost ≈ 1 for fresh data)."""
    query_emb = make_embedding(42, dims=10)
    # Create 3 embeddings with known similarity to query
    emb_close = make_embedding(42, dims=10)     # identical → sim ≈ 1.0
    emb_medium = make_embedding(43, dims=10)     # different seed → lower sim
    emb_far = make_embedding(99, dims=10)        # very different

    async with get_db() as db:
        await insert_source(db, "s1", title="Close Source")
        await insert_source(db, "s2", title="Medium Source")
        await insert_source(db, "s3", title="Far Source")
        await insert_chunk(db, "c1", "s1", "close content", emb_close)
        await insert_chunk(db, "c2", "s2", "medium content", emb_medium)
        await insert_chunk(db, "c3", "s3", "far content", emb_far)

    with patch("app.search.embed_text", new_callable=AsyncMock, return_value=query_emb):
        from app.search import vector_search
        results = await vector_search("test query", top_k=3)

    assert len(results) == 3
    # First result should be the closest (identical embedding)
    assert results[0]["source_id"] == "s1"
    # Scores should be descending
    assert results[0]["score"] >= results[1]["score"] >= results[2]["score"]


@pytest.mark.asyncio
async def test_vector_search_dedup_per_source(setup_temp_db):
    """Two chunks from the same source → only the best-scoring one returned."""
    query_emb = make_embedding(42, dims=10)
    emb_best = make_embedding(42, dims=10)   # identical → highest score
    emb_worse = make_embedding(99, dims=10)  # different → lower score

    async with get_db() as db:
        await insert_source(db, "s1", title="Same Source")
        await insert_chunk(db, "c1", "s1", "best chunk", emb_best, chunk_index=0)
        await insert_chunk(db, "c2", "s1", "worse chunk", emb_worse, chunk_index=1)

    with patch("app.search.embed_text", new_callable=AsyncMock, return_value=query_emb):
        from app.search import vector_search
        results = await vector_search("test query", top_k=5)

    assert len(results) == 1
    assert results[0]["chunk_id"] == "c1"
    assert results[0]["content"] == "best chunk"


@pytest.mark.asyncio
async def test_vector_search_time_boost(setup_temp_db):
    """Older source scores lower than recent one with same semantic similarity."""
    query_emb = make_embedding(42, dims=10)
    same_emb = make_embedding(42, dims=10)  # identical to query for both

    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now - timedelta(days=60)).isoformat()

    async with get_db() as db:
        await insert_source(db, "s-recent", title="Recent", ingested_at=recent)
        await insert_source(db, "s-old", title="Old", ingested_at=old)
        await insert_chunk(db, "c-recent", "s-recent", "recent content", same_emb)
        await insert_chunk(db, "c-old", "s-old", "old content", same_emb)

    with patch("app.search.embed_text", new_callable=AsyncMock, return_value=query_emb):
        from app.search import vector_search
        results = await vector_search("test", top_k=5)

    assert len(results) == 2
    assert results[0]["source_id"] == "s-recent"
    assert results[1]["source_id"] == "s-old"
    assert results[0]["score"] > results[1]["score"]


# --- Wiki Search Tests ---

@pytest.mark.asyncio
async def test_wiki_search_empty(setup_temp_db):
    """No wiki pages → empty list."""
    query_emb = make_embedding(0)
    with patch("app.search.embed_text", new_callable=AsyncMock, return_value=query_emb):
        from app.search import wiki_search
        results = await wiki_search("anything")
    assert results == []


@pytest.mark.asyncio
async def test_wiki_search_returns_pages(setup_temp_db):
    """Wiki page with matching embedding should be returned."""
    query_emb = make_embedding(42, dims=10)
    page_emb = make_embedding(42, dims=10)  # identical → high score

    async with get_db() as db:
        await insert_wiki_page(db, "test-topic", "Test Topic", "Some wiki content", page_emb)

    with patch("app.search.embed_text", new_callable=AsyncMock, return_value=query_emb):
        from app.search import wiki_search
        results = await wiki_search("test query", top_k=2)

    assert len(results) == 1
    assert results[0]["slug"] == "test-topic"
    assert results[0]["title"] == "Test Topic"
    assert results[0]["content"] == "Some wiki content"
    assert results[0]["score"] > 0.9  # near-identical embeddings


# --- Synthesis Tests ---

@pytest.mark.asyncio
async def test_synthesize_empty_kb(setup_temp_db):
    """When both searches return empty → 'don't have knowledge' message."""
    with patch("app.synthesis.vector_search", new_callable=AsyncMock, return_value=[]), \
         patch("app.synthesis.wiki_search", new_callable=AsyncMock, return_value=[]):
        from app.synthesis import synthesize_answer
        result = await synthesize_answer("what is life?")

    assert "don't have any knowledge" in result["answer"]
    assert result["sources"] == []
    assert result["wiki_pages"] == []
    assert result["related_topics"] == []


@pytest.mark.asyncio
async def test_synthesize_with_sources(setup_temp_db):
    """Mock search + Gemini Pro → verify answer structure."""
    mock_sources = [
        {
            "chunk_id": "c1", "source_id": "s1", "content": "AI is transforming everything",
            "score": 0.95, "source_title": "AI Article", "source_url": "https://example.com/ai",
        }
    ]
    mock_wiki = [
        {"slug": "ai", "title": "Artificial Intelligence", "content": "AI overview", "score": 0.9}
    ]

    mock_response = MagicMock()
    mock_response.text = "AI is indeed transforming everything. Related topics: [[machine learning]], [[automation]]"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.synthesis.vector_search", new_callable=AsyncMock, return_value=mock_sources), \
         patch("app.synthesis.wiki_search", new_callable=AsyncMock, return_value=mock_wiki), \
         patch("app.synthesis.genai.Client", return_value=mock_client):
        from app.synthesis import synthesize_answer
        result = await synthesize_answer("what is AI?")

    assert "transforming" in result["answer"]
    assert len(result["sources"]) == 1
    assert result["sources"][0]["id"] == "s1"
    assert result["sources"][0]["relevance"] == 0.95
    assert result["wiki_pages"] == ["ai"]
    assert "machine learning" in result["related_topics"]
    assert "automation" in result["related_topics"]


# --- Endpoint Test ---

@pytest.mark.asyncio
async def test_query_endpoint_mocked(client):
    """POST /query → 200 with QueryResponse format."""
    mock_result = {
        "answer": "Test answer with [[topic1]]",
        "sources": [{"id": "s1", "title": "Source 1", "url": "https://example.com", "relevance": 0.9}],
        "wiki_pages": ["topic1"],
        "related_topics": ["topic1"],
    }

    # main.py does `from app.synthesis import synthesize_answer` lazily inside the endpoint,
    # so we patch the function on the synthesis module itself
    with patch("app.synthesis.synthesize_answer", new_callable=AsyncMock, return_value=mock_result):
        resp = await client.post("/query", json={"query": "test question"})

    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "sources" in data
    assert "wiki_pages" in data
    assert "related_topics" in data
