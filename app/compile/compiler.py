"""Wiki page compilation via Claude Opus."""
import json
import logging
import os
import re
from datetime import datetime, timezone

import anthropic

from app import config
from app.database import get_db
from app.embedding import embed_text, serialize_embedding
from app.enrichment.pipeline import regenerate_index
from app.compile.prompts import COMPILE_PROMPT

logger = logging.getLogger(__name__)


def get_anthropic_client():
    """Get or create Anthropic client."""
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


async def rebuild_backlinks() -> dict:
    """Scan all wiki page content for [[wikilinks]] and build a backlinks index.

    Returns a dict mapping target slugs to lists of referring slugs:
        { "target-slug": ["referring-slug-1", "referring-slug-2", ...] }

    Writes the result to wiki/_backlinks.json.
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT slug, content FROM wiki_pages WHERE content IS NOT NULL")
        pages = await cursor.fetchall()

    backlinks: dict[str, list[str]] = {}

    for slug, content in pages:
        if not content:
            continue
        # Find all [[wikilink]] references in this page
        # Handles both [[slug]] and [[slug|display text]] formats
        linked_slugs = re.findall(r'\[\[([^\]]+)\]\]', content)
        for raw_target in linked_slugs:
            # Normalize piped wikilinks: [[slug|display text]] → slug
            target = raw_target.split('|')[0].strip()
            if not target or target == slug:
                # Skip self-references
                continue
            if target not in backlinks:
                backlinks[target] = []
            if slug not in backlinks[target]:
                backlinks[target].append(slug)

    # Write to disk
    os.makedirs(config.WIKI_DIR, exist_ok=True)
    backlinks_path = config.WIKI_DIR / "_backlinks.json"
    backlinks_path.write_text(json.dumps(backlinks, indent=2, sort_keys=True))

    logger.info(f"Rebuilt backlinks index: {len(backlinks)} targets across {len(pages)} pages")
    return backlinks


async def compile_topic(slug: str) -> dict:
    """Compile a single wiki page from its linked sources.

    1. Get wiki page from DB
    2. Get all linked sources (via wiki_source_links)
    3. Get _index.md content (existing topics for cross-linking)
    4. Load current backlinks for this topic (who links here)
    5. Build prompt with sources + existing page + backlinks (if updating)
    6. Call Claude Opus
    7. Save content to wiki_pages.content (DB = source of truth)
    8. Update source_count, last_compiled_at, stale = 0
    9. Re-embed the wiki page content → update wiki_pages.embedding
    10. Write wiki/{slug}.md to disk (projection)
    11. Regenerate _index.md
    12. Rebuild backlinks index
    13. Check for SPLIT_SUGGESTED in output → log if found

    Returns: {"slug": str, "title": str, "source_count": int, "compiled": True}
    """
    # 1. Get wiki page
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, slug, title, content FROM wiki_pages WHERE slug = ?",
            (slug,),
        )
        page = await cursor.fetchone()

    if not page:
        raise ValueError(f"Wiki page '{slug}' not found")

    page_id, page_slug, page_title, existing_content = page[0], page[1], page[2], page[3]

    # 2. Get linked sources
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT s.id, s.title, s.url, s.summary, s.key_insights, s.ingested_at, s.author
               FROM sources s
               JOIN wiki_source_links wsl ON s.id = wsl.source_id
               WHERE wsl.wiki_page_id = ?
               ORDER BY s.ingested_at DESC""",
            (page_id,),
        )
        sources = await cursor.fetchall()

    source_count = len(sources)

    # 3. Get _index.md content
    index_path = config.WIKI_DIR / "_index.md"
    index_context = ""
    if index_path.exists():
        index_context = index_path.read_text()

    # 4. Load current backlinks for this topic
    backlinks_path = config.WIKI_DIR / "_backlinks.json"
    backlinks_context = ""
    if backlinks_path.exists():
        try:
            backlinks = json.loads(backlinks_path.read_text())
            referring_pages = backlinks.get(slug, [])
            if referring_pages:
                backlinks_context = "Pages that currently link to this topic:\n"
                backlinks_context += "\n".join(f"- [[{s}]]" for s in referring_pages)
            else:
                backlinks_context = "No other pages currently link to this topic."
        except (json.JSONDecodeError, Exception):
            backlinks_context = "Backlinks index not available."
    else:
        backlinks_context = "No backlinks index yet — this may be the first compilation."

    # 5. Build prompt
    sources_context = ""
    for s in sources:
        sources_context += f"\n### {s[1] or 'Untitled'}\n"
        if s[6]:  # author
            sources_context += f"Author: {s[6]}\n"
        if s[2]:  # url
            sources_context += f"URL: {s[2]}\n"
        if s[5]:  # ingested_at
            sources_context += f"Date: {s[5]}\n"
        if s[3]:  # summary
            sources_context += f"Summary: {s[3]}\n"
        if s[4]:  # key_insights
            sources_context += f"Key insights: {s[4]}\n"

    prompt = COMPILE_PROMPT.format(
        existing_content=existing_content or "None — this is a new page.",
        topic_title=page_title,
        sources_context=sources_context or "No sources linked yet.",
        index_context=index_context or "No other topics yet.",
        backlinks_context=backlinks_context,
    )

    # 6. Call Claude Opus
    client = get_anthropic_client()
    import asyncio
    response = await asyncio.to_thread(
        client.messages.create,
        model=config.OPUS_MODEL,
        max_tokens=8192,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    compiled_content = response.content[0].text

    # 7 & 8. Save to DB
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE wiki_pages SET content = ?, source_count = ?, last_compiled_at = ?, stale = 0 WHERE id = ?",
            (compiled_content, source_count, now, page_id),
        )
        await db.commit()

    # 9. Re-embed
    embedding = await embed_text(compiled_content[:8000])
    serialized = serialize_embedding(embedding)
    async with get_db() as db:
        await db.execute(
            "UPDATE wiki_pages SET embedding = ? WHERE id = ?",
            (serialized, page_id),
        )
        await db.commit()

    # 10. Write to disk
    os.makedirs(config.WIKI_DIR, exist_ok=True)
    wiki_file = config.WIKI_DIR / f"{slug}.md"
    wiki_file.write_text(compiled_content)

    # 11. Regenerate index
    await regenerate_index()

    # 12. Rebuild backlinks index
    await rebuild_backlinks()

    # 13. Check for SPLIT_SUGGESTED
    result = {
        "slug": slug,
        "title": page_title,
        "source_count": source_count,
        "compiled": True,
    }

    split_match = re.search(r'SPLIT_SUGGESTED:\s*\[(.+?)\]', compiled_content)
    if split_match:
        try:
            # Parse the suggested splits
            split_text = "[" + split_match.group(1) + "]"
            suggested = json.loads(split_text)
            result["split_suggested"] = suggested
            logger.warning(f"Topic '{slug}' suggests splitting into: {suggested}")
        except (json.JSONDecodeError, Exception):
            result["split_suggested"] = split_match.group(1)
            logger.warning(f"Topic '{slug}' suggests splitting: {split_match.group(1)}")

    return result


async def compile_nightly() -> dict:
    """Compile all stale topics.
    Returns: {"compiled": int, "failed": int, "skipped": int, "details": [...]}
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT slug FROM wiki_pages WHERE stale = 1")
        stale = await cursor.fetchall()

    results = {"compiled": 0, "failed": 0, "skipped": 0, "details": []}
    for row in stale:
        try:
            result = await compile_topic(row[0])
            results["compiled"] += 1
            results["details"].append(result)
        except Exception as e:
            results["failed"] += 1
            results["details"].append({"slug": row[0], "error": str(e)})
            logger.error(f"Failed to compile '{row[0]}': {e}")

    return results
