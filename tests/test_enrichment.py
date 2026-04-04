"""Tests for the 4-stage enrichment pipeline."""
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.embedding import (
    sample_content,
    chunk_text,
    serialize_embedding,
    deserialize_embedding,
    cosine_similarity,
)
from app.database import get_db


# --- Helpers ---

def mock_gemini_response(text: str):
    """Create a mock Gemini response."""
    response = MagicMock()
    response.text = text
    return response


def mock_embed_response(values: list[float]):
    """Create a mock embedding response."""
    response = MagicMock()
    embedding = MagicMock()
    embedding.values = values
    response.embeddings = [embedding]
    return response


def mock_batch_embed_response(values_list: list[list[float]]):
    """Create a mock batch embedding response."""
    response = MagicMock()
    embeddings = []
    for values in values_list:
        emb = MagicMock()
        emb.values = values
        embeddings.append(emb)
    response.embeddings = embeddings
    return response


async def _insert_test_source(source_id: str, raw_content: str = "Test content for enrichment pipeline.", title: str = "Test Title", author: str = "Test Author"):
    """Insert a test source row."""
    import hashlib
    content_hash = hashlib.sha256(raw_content.encode()).hexdigest()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO sources (id, url, source_type, title, author, content_hash, raw_content, enrichment_status)
               VALUES (?, ?, 'article', ?, ?, ?, ?, 'pending')""",
            (source_id, f"https://example.com/{source_id}", title, author, content_hash, raw_content)
        )
        await db.commit()


# --- sample_content tests ---

@pytest.mark.asyncio
async def test_sample_content_short():
    """Text <8000 chars returns as-is."""
    text = "Short text " * 100  # ~1100 chars
    result = sample_content(text)
    assert result == text


@pytest.mark.asyncio
async def test_sample_content_long():
    """Text >8000 chars returns sampled with [...] markers."""
    text = "A" * 10000
    result = sample_content(text, max_chars=8000)
    assert "[...]" in result
    assert len(result) < len(text)


# --- chunk_text tests ---

@pytest.mark.asyncio
async def test_chunk_text_short():
    """Text <4000 chars → 1 chunk."""
    text = "Short text for chunking."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0]["text"] == text
    assert "token_count" in chunks[0]


@pytest.mark.asyncio
async def test_chunk_text_medium():
    """Text ~8000 chars → 2-3 chunks with overlap."""
    # Build text with paragraph breaks
    paragraphs = [f"Paragraph {i}. " + "x" * 500 for i in range(16)]
    text = "\n\n".join(paragraphs)  # ~8000+ chars
    chunks = chunk_text(text)
    assert 2 <= len(chunks) <= 4
    # Check overlap exists between consecutive chunks
    if len(chunks) >= 2:
        # The end of chunk 0 should overlap with start of chunk 1
        end_of_first = chunks[0]["text"][-100:]
        assert end_of_first in chunks[1]["text"]


@pytest.mark.asyncio
async def test_chunk_text_long():
    """Text ~20000 chars → multiple chunks."""
    paragraphs = [f"Paragraph {i}. " + "y" * 500 for i in range(40)]
    text = "\n\n".join(paragraphs)  # ~20000+ chars
    chunks = chunk_text(text)
    assert len(chunks) >= 4


# --- serialize/deserialize embedding tests ---

@pytest.mark.asyncio
async def test_serialize_deserialize_embedding():
    """Round-trip: list → bytes → list matches."""
    original = [0.1, 0.2, 0.3, -0.5, 1.0]
    blob = serialize_embedding(original)
    assert isinstance(blob, bytes)
    restored = deserialize_embedding(blob)
    assert len(restored) == len(original)
    for a, b in zip(original, restored):
        assert abs(a - b) < 1e-6


# --- cosine_similarity tests ---

@pytest.mark.asyncio
async def test_cosine_similarity_identical():
    """Same vector → ~1.0."""
    vec = [1.0, 2.0, 3.0, 4.0]
    score = cosine_similarity(vec, vec)
    assert abs(score - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_cosine_similarity_orthogonal():
    """Orthogonal vectors → ~0.0."""
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    score = cosine_similarity(a, b)
    assert abs(score) < 1e-6


# --- Stage tests ---

@pytest.mark.asyncio
async def test_stage_metadata(setup_temp_db):
    """Metadata stage cleans title and author."""
    source_id = str(uuid.uuid4())
    await _insert_test_source(source_id, title="  Messy Title  ", author="  Author Name  ")

    from app.enrichment.pipeline import _stage_metadata
    await _stage_metadata(source_id)

    async with get_db() as db:
        cursor = await db.execute("SELECT title, author FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    assert row[0] == "Messy Title"
    assert row[1] == "Author Name"


@pytest.mark.asyncio
@patch("app.enrichment.pipeline.get_gemini_client")
async def test_stage_summary_mocked(mock_client_fn, setup_temp_db):
    """Summary stage stores summary and key_insights via mocked Gemini."""
    source_id = str(uuid.uuid4())
    await _insert_test_source(source_id, raw_content="Some article content about AI and startups." * 10)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_gemini_response(
        json.dumps({
            "summary": "This article discusses AI in startups.",
            "key_insights": ["AI is transformative", "Startups should adopt early"],
            "author": None
        })
    )
    mock_client_fn.return_value = mock_client

    from app.enrichment.pipeline import _stage_summary
    await _stage_summary(source_id)

    async with get_db() as db:
        cursor = await db.execute("SELECT summary, key_insights FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    assert row[0] == "This article discusses AI in startups."
    insights = json.loads(row[1])
    assert len(insights) == 2


@pytest.mark.asyncio
@patch("app.embedding.get_gemini_client")
@patch("app.enrichment.pipeline.get_gemini_client")
async def test_stage_extraction_mocked(mock_pipeline_client_fn, mock_embed_client_fn, setup_temp_db):
    """Extraction stage creates entities and topics via mocked Gemini + embedding."""
    source_id = str(uuid.uuid4())
    await _insert_test_source(source_id, title="AI Startups Guide", raw_content="Content about AI startups")

    # Set summary (extraction needs it)
    async with get_db() as db:
        await db.execute(
            "UPDATE sources SET summary = ?, key_insights = ? WHERE id = ?",
            ("Summary about AI", json.dumps(["insight1"]), source_id)
        )
        await db.commit()

    # Mock pipeline Gemini client (for extraction LLM call)
    mock_pipeline = MagicMock()
    mock_pipeline.models.generate_content.return_value = mock_gemini_response(
        json.dumps({
            "entities": [
                {"type": "concept", "name": "Artificial Intelligence"},
                {"type": "company", "name": "OpenAI"}
            ],
            "topics": [],
            "new_topics": [{"slug": "ai-startups", "title": "AI Startups"}]
        })
    )
    mock_pipeline_client_fn.return_value = mock_pipeline

    # Mock embedding client
    mock_embed = MagicMock()
    mock_embed.models.embed_content.return_value = mock_embed_response([0.1] * 3072)
    mock_embed_client_fn.return_value = mock_embed

    from app.enrichment.pipeline import _stage_extraction
    await _stage_extraction(source_id)

    # Verify entities
    async with get_db() as db:
        cursor = await db.execute("SELECT entity_type, entity_name FROM entities WHERE source_id = ?", (source_id,))
        entities = await cursor.fetchall()
    assert len(entities) == 2

    # Verify wiki page created
    async with get_db() as db:
        cursor = await db.execute("SELECT slug, title FROM wiki_pages WHERE slug = 'ai-startups'")
        page = await cursor.fetchone()
    assert page is not None
    assert page[1] == "AI Startups"

    # Verify source topics updated
    async with get_db() as db:
        cursor = await db.execute("SELECT topics FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    topics = json.loads(row[0])
    assert "ai-startups" in topics


@pytest.mark.asyncio
@patch("app.embedding.get_gemini_client")
@patch("app.enrichment.pipeline.get_gemini_client")
async def test_full_pipeline_mocked(mock_pipeline_client_fn, mock_embed_client_fn, setup_temp_db):
    """Full pipeline runs all 4 stages and marks enrichment_status = 'complete'."""
    source_id = str(uuid.uuid4())
    await _insert_test_source(source_id, raw_content="Full pipeline test content for AI article." * 5)

    # Mock pipeline Gemini client
    mock_pipeline = MagicMock()

    def gen_content_side_effect(*args, **kwargs):
        prompt = kwargs.get("contents", args[0] if args else "")
        if isinstance(prompt, str) and "Summarize" in prompt:
            return mock_gemini_response(json.dumps({
                "summary": "Test summary",
                "key_insights": ["insight1"],
                "author": None
            }))
        else:
            return mock_gemini_response(json.dumps({
                "entities": [{"type": "concept", "name": "AI"}],
                "topics": [],
                "new_topics": [{"slug": "test-topic", "title": "Test Topic"}]
            }))

    mock_pipeline.models.generate_content.side_effect = gen_content_side_effect
    mock_pipeline_client_fn.return_value = mock_pipeline

    # Mock embedding client
    mock_embed = MagicMock()
    mock_embed.models.embed_content.return_value = mock_embed_response([0.5] * 3072)
    mock_embed_client_fn.return_value = mock_embed

    from app.enrichment.pipeline import run_enrichment
    await run_enrichment(source_id)

    # Verify enrichment status
    async with get_db() as db:
        cursor = await db.execute("SELECT enrichment_status FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    assert row[0] == "complete"

    # Verify enrichment jobs
    async with get_db() as db:
        cursor = await db.execute("SELECT stage, status FROM enrichment_jobs WHERE source_id = ? ORDER BY created_at", (source_id,))
        jobs = await cursor.fetchall()
    assert len(jobs) == 4
    for job in jobs:
        assert job[1] == "complete"


@pytest.mark.asyncio
@patch("app.enrichment.pipeline._stage_metadata")
async def test_pipeline_failure_marks_failed(mock_metadata, setup_temp_db):
    """Pipeline failure marks enrichment_status = 'failed'."""
    source_id = str(uuid.uuid4())
    await _insert_test_source(source_id)

    mock_metadata.side_effect = Exception("Metadata stage exploded")

    from app.enrichment.pipeline import run_enrichment
    await run_enrichment(source_id)

    async with get_db() as db:
        cursor = await db.execute("SELECT enrichment_status FROM sources WHERE id = ?", (source_id,))
        row = await cursor.fetchone()
    assert row[0] == "failed"

    # Verify the job is marked failed
    async with get_db() as db:
        cursor = await db.execute("SELECT status, error FROM enrichment_jobs WHERE source_id = ? AND stage = 'metadata'", (source_id,))
        job = await cursor.fetchone()
    assert job[0] == "failed"
    assert "exploded" in job[1]
