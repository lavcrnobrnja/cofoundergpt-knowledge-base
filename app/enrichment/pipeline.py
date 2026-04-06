"""4-stage enrichment pipeline orchestrator."""
import json
import logging
import uuid
from datetime import datetime, timezone

from google import genai
from google.genai import types

from app import config
from app.database import get_db
from app.embedding import (
    sample_content,
    chunk_text,
    embed_text,
    embed_texts,
    cosine_similarity,
    serialize_embedding,
    deserialize_embedding,
)
from app.enrichment.prompts import SUMMARY_PROMPT, EXTRACTION_PROMPT

logger = logging.getLogger(__name__)


def get_gemini_client():
    """Get or create Gemini client."""
    return genai.Client(api_key=config.GEMINI_API_KEY)


async def run_enrichment(source_id: str):
    """Run all 4 enrichment stages for a source. Each stage retries up to 3 times."""
    stages = ["metadata", "summary", "extraction", "vectors"]

    # Set status to processing at the start
    async with get_db() as db:
        await db.execute(
            "UPDATE sources SET enrichment_status = 'processing' WHERE id = ?",
            (source_id,),
        )
        await db.commit()

    for stage in stages:
        job_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                "INSERT INTO enrichment_jobs (id, source_id, stage, status) VALUES (?, ?, ?, 'pending')",
                (job_id, source_id, stage),
            )
            await db.commit()

        success = False
        for attempt in range(1, 4):
            try:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE enrichment_jobs SET status = 'running', attempt = ? WHERE id = ?",
                        (attempt, job_id),
                    )
                    await db.commit()

                if stage == "metadata":
                    await _stage_metadata(source_id)
                elif stage == "summary":
                    await _stage_summary(source_id)
                elif stage == "extraction":
                    await _stage_extraction(source_id)
                elif stage == "vectors":
                    await _stage_vectors(source_id)

                async with get_db() as db:
                    await db.execute(
                        "UPDATE enrichment_jobs SET status = 'complete', completed_at = datetime('now') WHERE id = ?",
                        (job_id,),
                    )
                    await db.commit()
                success = True
                break

            except Exception as e:
                logger.error(f"Stage {stage} attempt {attempt} failed for {source_id}: {e}")
                async with get_db() as db:
                    await db.execute(
                        "UPDATE enrichment_jobs SET error = ? WHERE id = ?",
                        (str(e), job_id),
                    )
                    await db.commit()

        if not success:
            async with get_db() as db:
                await db.execute(
                    "UPDATE enrichment_jobs SET status = 'failed' WHERE id = ?", (job_id,)
                )
                await db.execute(
                    "UPDATE sources SET enrichment_status = 'failed' WHERE id = ?",
                    (source_id,),
                )
                await db.commit()
            return

    # All stages passed
    async with get_db() as db:
        await db.execute(
            "UPDATE sources SET enrichment_status = 'complete' WHERE id = ?",
            (source_id,),
        )
        await db.commit()


async def _stage_metadata(source_id: str):
    """Normalize title, author, publish date. No LLM — parse from existing data."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT title, author, published_at, raw_content FROM sources WHERE id = ?",
            (source_id,),
        )
        row = await cursor.fetchone()

    title = (row[0] or "").strip()[:500] or None
    author = (row[1] or "").strip() or None

    async with get_db() as db:
        await db.execute(
            "UPDATE sources SET title = ?, author = ? WHERE id = ?",
            (title, author, source_id),
        )
        await db.commit()


async def _stage_summary(source_id: str):
    """Generate summary + key insights via Gemini Flash."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT raw_content, author FROM sources WHERE id = ?", (source_id,)
        )
        row = await cursor.fetchone()

    raw_content = row[0]
    has_author = bool(row[1])

    content = sample_content(raw_content, max_chars=8000)

    client = get_gemini_client()
    prompt = SUMMARY_PROMPT.format(content=content)

    import asyncio
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=config.FLASH_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )

    result = json.loads(response.text)

    async with get_db() as db:
        updates = {
            "summary": result.get("summary"),
            "key_insights": json.dumps(result.get("key_insights", [])),
        }
        
        # Capture guests
        guests_raw = result.get("guests")
        if guests_raw and isinstance(guests_raw, list) and len(guests_raw) > 0:
            updates["guests"] = json.dumps(guests_raw)
            
        if not has_author and result.get("author"):
            updates["author"] = result["author"]

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        await db.execute(
            f"UPDATE sources SET {set_clause} WHERE id = ?",
            (*updates.values(), source_id),
        )
        await db.commit()


async def _stage_extraction(source_id: str):
    """Extract entities + assign topics via Gemini Flash. Run similarity gate for new topics."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT title, summary, key_insights, raw_content FROM sources WHERE id = ?",
            (source_id,),
        )
        source = await cursor.fetchone()

        cursor = await db.execute("SELECT slug, title FROM wiki_pages")
        existing_topics = await cursor.fetchall()

    topic_list = (
        "\n".join(f"- {row[0]}: {row[1]}" for row in existing_topics)
        if existing_topics
        else "None yet"
    )

    content_sample = sample_content(source[3], max_chars=6000) if source[3] else ""

    client = get_gemini_client()
    prompt = EXTRACTION_PROMPT.format(
        title=source[0],
        summary=source[1],
        key_insights=source[2],
        content_sample=content_sample,
        existing_topics=topic_list,
    )

    import asyncio
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=config.FLASH_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )

    result = json.loads(response.text)

    # Insert entities
    async with get_db() as db:
        for entity in result.get("entities", [])[:20]:
            entity_id = str(uuid.uuid4())
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO entities (id, source_id, entity_type, entity_name) VALUES (?, ?, ?, ?)",
                    (entity_id, source_id, entity["type"], entity["name"]),
                )
            except Exception:
                pass
        await db.commit()

    # Process topics — similarity gate for new topics
    assigned_slugs = []

    # Validate existing topic assignments
    for slug in result.get("topics", [])[:4]:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT id FROM wiki_pages WHERE slug = ?", (slug,)
            )
            if await cursor.fetchone():
                assigned_slugs.append(slug)

    # Process new topic proposals through similarity gate
    for topic in result.get("new_topics", [])[:4]:
        if len(assigned_slugs) >= 4:
            break

        new_slug = topic["slug"]
        new_title = topic["title"]

        new_embedding = await embed_text(new_title)

        best_match = None
        best_score = 0.0

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT slug, title, embedding FROM wiki_pages WHERE embedding IS NOT NULL"
            )
            rows = await cursor.fetchall()

        for row in rows:
            existing_embedding = deserialize_embedding(row[2])
            score = cosine_similarity(new_embedding, existing_embedding)
            if score > best_score:
                best_score = score
                best_match = row[0]

        if best_score > 0.85:
            assigned_slugs.append(best_match)
        else:
            page_id = str(uuid.uuid4())
            serialized = serialize_embedding(new_embedding)
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO wiki_pages (id, slug, title, embedding, stale) VALUES (?, ?, ?, ?, 0)",
                    (page_id, new_slug, new_title, serialized),
                )
                await db.commit()
            assigned_slugs.append(new_slug)

    # Create wiki_source_links
    async with get_db() as db:
        for slug in assigned_slugs:
            cursor = await db.execute(
                "SELECT id, content FROM wiki_pages WHERE slug = ?", (slug,)
            )
            page = await cursor.fetchone()
            if page:
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO wiki_source_links (wiki_page_id, source_id) VALUES (?, ?)",
                        (page[0], source_id),
                    )
                except Exception:
                    pass

                if page[1]:  # has content → mark stale
                    await db.execute(
                        "UPDATE wiki_pages SET stale = 1 WHERE id = ?", (page[0],)
                    )
                else:
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM wiki_source_links WHERE wiki_page_id = ?",
                        (page[0],),
                    )
                    count = (await cursor.fetchone())[0]
                    if count >= 3:
                        await db.execute(
                            "UPDATE wiki_pages SET stale = 1 WHERE id = ?", (page[0],)
                        )

                # Update source_count on wiki page
                count_cursor = await db.execute(
                    "SELECT COUNT(*) FROM wiki_source_links WHERE wiki_page_id = ?",
                    (page[0],),
                )
                new_count = (await count_cursor.fetchone())[0]
                await db.execute(
                    "UPDATE wiki_pages SET source_count = ? WHERE id = ?",
                    (new_count, page[0]),
                )
        await db.commit()

    # Update source topics
    async with get_db() as db:
        await db.execute(
            "UPDATE sources SET topics = ? WHERE id = ?",
            (json.dumps(assigned_slugs), source_id),
        )
        await db.commit()

    # Regenerate _index.md
    await regenerate_index()


async def _stage_vectors(source_id: str):
    """Chunk text and generate embeddings."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT raw_content FROM sources WHERE id = ?", (source_id,)
        )
        row = await cursor.fetchone()

    raw_content = row[0]
    chunks = chunk_text(raw_content)

    chunk_texts = [c["text"] for c in chunks]
    embeddings = await embed_texts(chunk_texts)

    async with get_db() as db:
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = str(uuid.uuid4())
            serialized = serialize_embedding(embedding)
            await db.execute(
                "INSERT INTO chunks (id, source_id, chunk_index, content, embedding, token_count) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    source_id,
                    i,
                    chunk["text"],
                    serialized,
                    chunk.get("token_count"),
                ),
            )
        await db.commit()


async def regenerate_index():
    """Regenerate wiki/_index.md from DB state."""
    import json
    import os

    os.makedirs(config.WIKI_DIR, exist_ok=True)

    async with get_db() as db:
        cursor = await db.execute(
            """SELECT slug, title, source_count, last_compiled_at, content
               FROM wiki_pages ORDER BY slug"""
        )
        pages = await cursor.fetchall()

        total_sources_cursor = await db.execute("SELECT COUNT(*) FROM sources")
        total_sources = (await total_sources_cursor.fetchone())[0]

    # Load backlinks index for backlink counts
    backlinks: dict = {}
    backlinks_path = config.WIKI_DIR / "_backlinks.json"
    if backlinks_path.exists():
        try:
            backlinks = json.loads(backlinks_path.read_text())
        except (json.JSONDecodeError, Exception):
            pass

    lines = [
        "# Knowledge Base Index",
        f"_Last updated: {datetime.now(timezone.utc).isoformat()}Z | Topics: {len(pages)} | Sources: {total_sources}_",
        "",
        "| Topic | Sources | Backlinks | Last Compiled | Summary |",
        "|---|---|---|---|---|",
    ]

    for page in pages:
        slug, title, source_count, last_compiled, content = page
        summary = title
        if content:
            for line in content.split("\n"):
                if line.strip() and not line.startswith("#") and not line.startswith("_"):
                    summary = line.strip()[:100]
                    break
        backlink_count = len(backlinks.get(slug, []))
        lines.append(
            f"| [[{slug}]] | {source_count} | {backlink_count} | {last_compiled or 'never'} | {summary} |"
        )

    index_path = config.WIKI_DIR / "_index.md"
    index_path.write_text("\n".join(lines))
