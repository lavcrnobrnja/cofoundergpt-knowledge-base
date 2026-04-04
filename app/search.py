"""Vector search + wiki search with time-boosted ranking."""
import math
from datetime import datetime, timezone

from app.database import get_db
from app.embedding import embed_text, deserialize_embedding, cosine_similarity


async def vector_search(query: str, top_k: int = 5) -> list[dict]:
    """Search source chunks by cosine similarity with time decay.

    1. Embed query via Gemini embedding model
    2. Load all chunk embeddings from DB
    3. Compute cosine similarity per chunk
    4. Apply time boost: score * exp(-0.023 * days_old) → 30-day half-life
    5. Deduplicate: max 1 chunk per source (best-scoring wins)
    6. Return top_k results
    """
    query_embedding = await embed_text(query)

    async with get_db() as db:
        cursor = await db.execute("""
            SELECT c.id, c.source_id, c.content, c.embedding,
                   s.title, s.url, s.ingested_at
            FROM chunks c
            JOIN sources s ON c.source_id = s.id
            WHERE c.embedding IS NOT NULL
        """)
        rows = await cursor.fetchall()

    scored = []
    for row in rows:
        chunk_embedding = deserialize_embedding(row[3])
        semantic_score = cosine_similarity(query_embedding, chunk_embedding)

        # Time boost — 30-day half-life
        ingested_at = datetime.fromisoformat(row[6])
        if ingested_at.tzinfo is None:
            ingested_at = ingested_at.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - ingested_at).days
        time_boost = math.exp(-0.023 * days_old)

        final_score = semantic_score * time_boost
        scored.append({
            "chunk_id": row[0],
            "source_id": row[1],
            "content": row[2],
            "score": final_score,
            "source_title": row[4],
            "source_url": row[5],
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate: max 1 chunk per source
    seen_sources: set[str] = set()
    deduped: list[dict] = []
    for item in scored:
        if item["source_id"] not in seen_sources:
            seen_sources.add(item["source_id"])
            deduped.append(item)
        if len(deduped) >= top_k:
            break

    return deduped


async def wiki_search(query: str, top_k: int = 2) -> list[dict]:
    """Search wiki pages by cosine similarity.

    Returns top_k wiki pages with full content, ranked by relevance.
    """
    query_embedding = await embed_text(query)

    async with get_db() as db:
        cursor = await db.execute("""
            SELECT slug, title, content, embedding
            FROM wiki_pages
            WHERE embedding IS NOT NULL AND content IS NOT NULL
        """)
        pages = await cursor.fetchall()

    scored = []
    for page in pages:
        page_embedding = deserialize_embedding(page[3])
        score = cosine_similarity(query_embedding, page_embedding)
        scored.append({
            "slug": page[0],
            "title": page[1],
            "content": page[2],
            "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
