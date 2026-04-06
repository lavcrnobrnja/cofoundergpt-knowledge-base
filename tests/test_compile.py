"""Tests for compile layer — wiki page compilation via Gemini Pro."""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from app.database import get_db
from app import config


async def _insert_wiki_page(slug, title, stale=1, content=None):
    """Helper to insert a wiki page."""
    page_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO wiki_pages (id, slug, title, stale, content) VALUES (?, ?, ?, ?, ?)",
            (page_id, slug, title, stale, content),
        )
        await db.commit()
    return page_id


async def _insert_source(source_id=None, title="Test Source", url=None):
    """Helper to insert a source."""
    sid = source_id or str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sources (id, url, source_type, title, content_hash, raw_content, enrichment_status, ingested_at, summary) VALUES (?, ?, 'article', ?, 'hash123', 'Some raw content here.', 'complete', datetime('now'), 'A summary of the source.')",
            (sid, url or f"https://example.com/{sid}", title),
        )
        await db.commit()
    return sid


async def _link_source_to_page(page_id, source_id):
    """Helper to link a source to a wiki page."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO wiki_source_links (wiki_page_id, source_id) VALUES (?, ?)",
            (page_id, source_id),
        )
        await db.commit()


def _mock_anthropic_response(text):
    """Create a mock Anthropic response."""
    mock_block = MagicMock()
    mock_block.text = text
    mock_resp = MagicMock()
    mock_resp.content = [mock_block]
    return mock_resp


def _mock_embed_result(dims=3072):
    """Create mock embedding result."""
    return [0.1] * dims


@pytest.mark.asyncio
async def test_compile_topic_mocked(setup_temp_db):
    """Mock Gemini Pro + embed_text, verify page content saved, stale=0, file written."""
    page_id = await _insert_wiki_page("test-topic", "Test Topic", stale=1)
    source_id = await _insert_source(title="Source One")
    await _link_source_to_page(page_id, source_id)

    compiled_content = """# Test Topic
## Overview
This is a test overview.

## Key Themes
- Theme one

## Connections
Related to [[other-topic]]

## Sources
| Date | Title | Key Takeaway |
|---|---|---|
| 2026-04-04 | Source One | Key takeaway |

## Open Questions
- What about X?"""

    with patch("app.compile.compiler.get_anthropic_client") as mock_client_fn, \
         patch("app.compile.compiler.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.compile.compiler.regenerate_index", new_callable=AsyncMock) as mock_regen:

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(compiled_content)
        mock_client_fn.return_value = mock_client
        mock_embed.return_value = _mock_embed_result()

        from app.compile.compiler import compile_topic
        result = await compile_topic("test-topic")

    assert result["slug"] == "test-topic"
    assert result["title"] == "Test Topic"
    assert result["compiled"] is True
    assert result["source_count"] == 1

    # Verify DB state
    async with get_db() as db:
        cursor = await db.execute("SELECT content, stale, source_count, last_compiled_at FROM wiki_pages WHERE slug = ?", ("test-topic",))
        row = await cursor.fetchone()
    
    assert row[0] == compiled_content  # content saved
    assert row[1] == 0  # stale = 0
    assert row[2] == 1  # source_count
    assert row[3] is not None  # last_compiled_at set

    # Verify embed was called
    mock_embed.assert_called_once()
    # Verify index regenerated
    mock_regen.assert_called_once()


@pytest.mark.asyncio
async def test_compile_nightly_no_stale(setup_temp_db):
    """No stale topics → compiled=0."""
    await _insert_wiki_page("clean-topic", "Clean Topic", stale=0)

    from app.compile.compiler import compile_nightly
    result = await compile_nightly()

    assert result["compiled"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == 0


@pytest.mark.asyncio
async def test_compile_nightly_with_stale(setup_temp_db):
    """Insert stale page, mock compile, verify compiled=1."""
    page_id = await _insert_wiki_page("stale-topic", "Stale Topic", stale=1)
    source_id = await _insert_source()
    await _link_source_to_page(page_id, source_id)

    compiled_content = "# Stale Topic\n## Overview\nCompiled."

    with patch("app.compile.compiler.get_anthropic_client") as mock_client_fn, \
         patch("app.compile.compiler.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.compile.compiler.regenerate_index", new_callable=AsyncMock):

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(compiled_content)
        mock_client_fn.return_value = mock_client
        mock_embed.return_value = _mock_embed_result()

        from app.compile.compiler import compile_nightly
        result = await compile_nightly()

    assert result["compiled"] == 1
    assert result["failed"] == 0
    assert len(result["details"]) == 1
    assert result["details"][0]["slug"] == "stale-topic"


@pytest.mark.asyncio
async def test_compile_detects_split_suggested(setup_temp_db):
    """Mock LLM response with SPLIT_SUGGESTED → detected in return value."""
    page_id = await _insert_wiki_page("big-topic", "Big Topic", stale=1)
    source_id = await _insert_source()
    await _link_source_to_page(page_id, source_id)

    compiled_content = """# Big Topic
## Overview
Too broad.

## Key Themes
- Many themes

SPLIT_SUGGESTED: ["Subtopic A", "Subtopic B"]"""

    with patch("app.compile.compiler.get_anthropic_client") as mock_client_fn, \
         patch("app.compile.compiler.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.compile.compiler.regenerate_index", new_callable=AsyncMock):

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(compiled_content)
        mock_client_fn.return_value = mock_client
        mock_embed.return_value = _mock_embed_result()

        from app.compile.compiler import compile_topic
        result = await compile_topic("big-topic")

    assert result["compiled"] is True
    assert "split_suggested" in result
    assert "Subtopic A" in result["split_suggested"]


@pytest.mark.asyncio
async def test_compile_writes_disk_file(setup_temp_db, tmp_path):
    """Verify wiki/{slug}.md written after compile."""
    # Point wiki dir to tmp
    config.WIKI_DIR = tmp_path / "wiki"

    page_id = await _insert_wiki_page("disk-topic", "Disk Topic", stale=1)
    source_id = await _insert_source()
    await _link_source_to_page(page_id, source_id)

    compiled_content = "# Disk Topic\n## Overview\nWritten to disk."

    with patch("app.compile.compiler.get_anthropic_client") as mock_client_fn, \
         patch("app.compile.compiler.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.compile.compiler.regenerate_index", new_callable=AsyncMock):

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(compiled_content)
        mock_client_fn.return_value = mock_client
        mock_embed.return_value = _mock_embed_result()

        from app.compile.compiler import compile_topic
        await compile_topic("disk-topic")

    wiki_file = config.WIKI_DIR / "disk-topic.md"
    assert wiki_file.exists()
    assert "Disk Topic" in wiki_file.read_text()


# --- Backlinks Tests ---

@pytest.mark.asyncio
async def test_rebuild_backlinks_basic(setup_temp_db):
    """rebuild_backlinks() extracts [[wikilinks]] from page content."""
    # Insert pages with cross-references
    await _insert_wiki_page(
        "page-a", "Page A", stale=0,
        content="# Page A\nThis references [[page-b]] and [[page-c]]."
    )
    await _insert_wiki_page(
        "page-b", "Page B", stale=0,
        content="# Page B\nThis references [[page-a]]."
    )
    await _insert_wiki_page(
        "page-c", "Page C", stale=0,
        content="# Page C\nNo outgoing links here."
    )

    from app.compile.compiler import rebuild_backlinks
    backlinks = await rebuild_backlinks()

    # page-b is linked from page-a
    assert "page-b" in backlinks
    assert "page-a" in backlinks["page-b"]

    # page-c is linked from page-a
    assert "page-c" in backlinks
    assert "page-a" in backlinks["page-c"]

    # page-a is linked from page-b
    assert "page-a" in backlinks
    assert "page-b" in backlinks["page-a"]

    # page-c has no outgoing links, so nothing links to it from page-c
    assert "page-c" not in backlinks["page-b"]  # page-b doesn't link to page-c


@pytest.mark.asyncio
async def test_rebuild_backlinks_no_links(setup_temp_db):
    """Pages with no [[wikilinks]] produce empty backlinks."""
    await _insert_wiki_page(
        "lonely-page", "Lonely Page", stale=0,
        content="# Lonely Page\nNo references to anything."
    )

    from app.compile.compiler import rebuild_backlinks
    backlinks = await rebuild_backlinks()

    # lonely-page has no outbound links, so it should not appear as a referring page
    for referring_pages in backlinks.values():
        assert "lonely-page" not in referring_pages


@pytest.mark.asyncio
async def test_rebuild_backlinks_circular(setup_temp_db):
    """Circular references are handled correctly — no duplicates."""
    await _insert_wiki_page(
        "alpha", "Alpha", stale=0,
        content="# Alpha\nSee [[beta]] for more."
    )
    await _insert_wiki_page(
        "beta", "Beta", stale=0,
        content="# Beta\nRelated to [[alpha]]."
    )

    from app.compile.compiler import rebuild_backlinks
    backlinks = await rebuild_backlinks()

    # alpha is linked from beta
    assert "alpha" in backlinks
    assert "beta" in backlinks["alpha"]
    assert backlinks["alpha"].count("beta") == 1  # no duplicates

    # beta is linked from alpha
    assert "beta" in backlinks
    assert "alpha" in backlinks["beta"]
    assert backlinks["beta"].count("alpha") == 1  # no duplicates


@pytest.mark.asyncio
async def test_rebuild_backlinks_self_reference_ignored(setup_temp_db):
    """Self-references (page linking to itself) are ignored."""
    await _insert_wiki_page(
        "narcissus", "Narcissus", stale=0,
        content="# Narcissus\nSee [[narcissus]] — this page references itself."
    )

    from app.compile.compiler import rebuild_backlinks
    backlinks = await rebuild_backlinks()

    # narcissus should not appear as its own backlink
    if "narcissus" in backlinks:
        assert "narcissus" not in backlinks["narcissus"]


@pytest.mark.asyncio
async def test_rebuild_backlinks_writes_json(setup_temp_db):
    """rebuild_backlinks() writes _backlinks.json to wiki dir."""
    await _insert_wiki_page(
        "writer", "Writer", stale=0,
        content="# Writer\nPoints to [[target-page]]."
    )
    await _insert_wiki_page(
        "target-page", "Target", stale=0,
        content="# Target\nNo outgoing links."
    )

    from app.compile.compiler import rebuild_backlinks
    await rebuild_backlinks()

    backlinks_file = config.WIKI_DIR / "_backlinks.json"
    assert backlinks_file.exists()

    loaded = json.loads(backlinks_file.read_text())
    assert "target-page" in loaded
    assert "writer" in loaded["target-page"]


@pytest.mark.asyncio
async def test_rebuild_backlinks_piped_wikilinks(setup_temp_db):
    """Piped wikilinks [[slug|display text]] are normalized to just the slug."""
    await _insert_wiki_page(
        "page-piped", "Page Piped", stale=0,
        content="# Page Piped\nSee [[artificial-intelligence|AI agents]] and [[systems-thinking|systemic]] for more."
    )
    await _insert_wiki_page(
        "artificial-intelligence", "AI", stale=0,
        content="# AI\nNo links."
    )
    await _insert_wiki_page(
        "systems-thinking", "Systems Thinking", stale=0,
        content="# Systems Thinking\nNo links."
    )

    from app.compile.compiler import rebuild_backlinks
    backlinks = await rebuild_backlinks()

    # Should normalize to slug only — no "artificial-intelligence|AI agents" keys
    assert "artificial-intelligence|AI agents" not in backlinks
    assert "systems-thinking|systemic" not in backlinks

    # The actual slugs should have backlinks
    assert "artificial-intelligence" in backlinks
    assert "page-piped" in backlinks["artificial-intelligence"]
    assert "systems-thinking" in backlinks
    assert "page-piped" in backlinks["systems-thinking"]


@pytest.mark.asyncio
async def test_compile_rebuilds_backlinks(setup_temp_db):
    """compile_topic() calls rebuild_backlinks() after compilation."""
    page_id = await _insert_wiki_page("backlink-test", "Backlink Test", stale=1)
    source_id = await _insert_source(title="BL Source")
    await _link_source_to_page(page_id, source_id)

    compiled_content = "# Backlink Test\nThis topic links to [[other-topic]]."

    with patch("app.compile.compiler.get_anthropic_client") as mock_client_fn, \
         patch("app.compile.compiler.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.compile.compiler.regenerate_index", new_callable=AsyncMock):

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(compiled_content)
        mock_client_fn.return_value = mock_client
        mock_embed.return_value = _mock_embed_result()

        from app.compile.compiler import compile_topic
        result = await compile_topic("backlink-test")

    assert result["compiled"] is True

    # _backlinks.json should now exist with the link from backlink-test → other-topic
    backlinks_file = config.WIKI_DIR / "_backlinks.json"
    assert backlinks_file.exists()
    loaded = json.loads(backlinks_file.read_text())
    assert "other-topic" in loaded
    assert "backlink-test" in loaded["other-topic"]
