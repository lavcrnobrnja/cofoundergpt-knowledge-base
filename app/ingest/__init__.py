"""Ingest module — source type detection and dispatcher."""
import hashlib
import json
import uuid
from urllib.parse import urlparse

from app.database import get_db
from app.models import IngestRequest, IngestResponse


def detect_source_type(url: str) -> str:
    """Detect source type from URL pattern."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""

    # YouTube
    if host in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if "/watch" in path or "/shorts/" in path:
            return "youtube"
    if host in ("youtu.be",):
        return "youtube"

    # Tweet / X
    if host in ("x.com", "www.x.com", "twitter.com", "www.twitter.com"):
        return "tweet"

    # Substack
    if host.endswith(".substack.com"):
        return "substack"

    # Default
    return "article"


async def ingest_source(request: IngestRequest) -> tuple[IngestResponse, int]:
    """Main ingest dispatcher. Returns (response, status_code)."""
    # Validate: must have url OR text
    if not request.url and not request.text:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Must provide url or text")

    # Determine source type and URL
    if request.text and not request.url:
        source_type = request.source_type or "quote"
        url = f"quote://{uuid.uuid4()}"
    else:
        url = request.url
        source_type = detect_source_type(url)

    # Dedup check
    async with get_db() as db:
        cursor = await db.execute("SELECT id, content_hash, user_context FROM sources WHERE url = ?", (url,))
        existing = await cursor.fetchone()

    # For quotes, dedup on content hash instead of URL (each quote gets unique URL)
    if source_type == "quote" and request.text:
        content_hash = hashlib.sha256(request.text.encode()).hexdigest()
        async with get_db() as db:
            cursor = await db.execute("SELECT id, content_hash, user_context FROM sources WHERE content_hash = ?", (content_hash,))
            existing = await cursor.fetchone()

    if existing:
        existing_id = existing[0] if isinstance(existing[0], str) else existing["id"]
        existing_hash = existing[1] if isinstance(existing[1], str) else existing["content_hash"]

        # Extract content to compare hash
        extracted = await _extract(source_type, url, request)
        new_hash = hashlib.sha256(extracted["raw_content"].encode()).hexdigest()

        if new_hash == existing_hash:
            # Same content — update user_context if new
            if request.user_context:
                async with get_db() as db:
                    await db.execute(
                        "UPDATE sources SET user_context = ? WHERE id = ?",
                        (request.user_context, existing_id)
                    )
                    await db.commit()

            # Get title for response
            async with get_db() as db:
                cursor = await db.execute("SELECT title, enrichment_status FROM sources WHERE id = ?", (existing_id,))
                row = await cursor.fetchone()

            return IngestResponse(
                id=existing_id,
                title=row[0],
                source_type=source_type,
                status=row[1],
            ), 200
        else:
            # Different content — delete old, re-ingest
            async with get_db() as db:
                await db.execute("DELETE FROM sources WHERE id = ?", (existing_id,))
                await db.commit()
    else:
        extracted = await _extract(source_type, url, request)

    # Validate content length
    if len(extracted["raw_content"]) < 20:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Extracted content too short (< 20 chars)")

    # Compute hash and insert
    content_hash = hashlib.sha256(extracted["raw_content"].encode()).hexdigest()
    source_id = str(uuid.uuid4())

    async with get_db() as db:
        await db.execute(
            """INSERT INTO sources (id, url, source_type, title, author, published_at,
               content_hash, raw_content, metadata, user_context, attribution, enrichment_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                source_id,
                url,
                source_type,
                extracted.get("title"),
                extracted.get("author"),
                extracted.get("published_at"),
                content_hash,
                extracted["raw_content"],
                json.dumps(extracted.get("metadata", {})),
                request.user_context,
                request.attribution,
            )
        )
        await db.commit()

    return IngestResponse(
        id=source_id,
        title=extracted.get("title"),
        source_type=source_type,
        status="pending",
    ), 201


async def _extract(source_type: str, url: str, request: IngestRequest) -> dict:
    """Route to the appropriate extractor."""
    if source_type == "quote":
        from app.ingest.quote import extract_quote
        return await extract_quote(request.text, request.attribution)
    elif source_type == "youtube":
        from app.ingest.youtube import extract_youtube
        return await extract_youtube(url)
    elif source_type == "tweet":
        from app.ingest.tweet import extract_tweet
        return await extract_tweet(url)
    else:
        # article, substack — both use article extractor
        from app.ingest.article import extract_article
        return await extract_article(url)
