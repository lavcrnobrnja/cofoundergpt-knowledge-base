"""Knowledge Base v2 — FastAPI application."""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from fastapi.responses import JSONResponse as _JSONResponse

from app.config import DB_PATH
from app.database import init_db, get_db
from app.models import HealthResponse, StatsResponse, IngestRequest, IngestResponse, QueryRequest, QueryResponse


_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global _start_time
    _start_time = time.time()
    await init_db()
    yield


app = FastAPI(title="Knowledge Base v2", lifespan=lifespan)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Return standard error format for 404s."""
    return JSONResponse(
        status_code=404,
        content={"error": "Not found", "detail": f"Path {request.url.path} not found"},
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    db_size = 0.0
    try:
        if DB_PATH.exists():
            db_size = DB_PATH.stat().st_size / (1024 * 1024)
    except Exception:
        pass

    return HealthResponse(
        status="ok",
        db_size_mb=round(db_size, 4),
        uptime_seconds=round(time.time() - _start_time, 2),
    )


@app.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(request: IngestRequest, background_tasks: BackgroundTasks):
    """Ingest a new source (URL or text)."""
    from app.ingest import ingest_source
    response, status_code = await ingest_source(request)
    
    if status_code == 201:
        # Trigger enrichment in background
        from app.enrichment.pipeline import run_enrichment
        background_tasks.add_task(run_enrichment, response.id)
        
    return _JSONResponse(content=response.model_dump(), status_code=status_code)


@app.get("/sources/{source_id}/pipeline")
async def get_pipeline_status(source_id: str):
    """Get the enrichment pipeline status for a source."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT stage, status, attempt, error, created_at, completed_at FROM enrichment_jobs WHERE source_id = ? ORDER BY created_at",
            (source_id,)
        )
        jobs = await cursor.fetchall()
    
    if not jobs:
        return JSONResponse(status_code=404, content={"error": "Source not found", "detail": "No pipeline jobs for this source"})
    
    return [
        {
            "stage": j[0], "status": j[1], "attempt": j[2],
            "error": j[3], "created_at": j[4], "completed_at": j[5]
        }
        for j in jobs
    ]


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """Ask a question and get a synthesized answer."""
    from app.synthesis import synthesize_answer
    result = await synthesize_answer(request.query)
    return QueryResponse(**result)


# --- Wiki Endpoints ---

@app.get("/wiki")
async def list_wiki_pages():
    """List all wiki pages."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT slug, title, source_count, last_compiled_at, stale FROM wiki_pages ORDER BY slug"
        )
        rows = await cursor.fetchall()
    return [
        {"slug": r[0], "title": r[1], "source_count": r[2],
         "last_compiled_at": r[3], "stale": bool(r[4])}
        for r in rows
    ]


@app.get("/wiki/{slug}")
async def get_wiki_page(slug: str):
    """Get a wiki page with linked sources."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT slug, title, content, source_count, last_compiled_at, stale FROM wiki_pages WHERE slug = ?",
            (slug,)
        )
        page = await cursor.fetchone()

    if not page:
        return JSONResponse(status_code=404, content={"error": "Not found", "detail": f"Wiki page '{slug}' not found"})

    # Get linked sources
    async with get_db() as db:
        cursor = await db.execute("""
            SELECT s.id, s.title, s.url, s.summary, s.ingested_at
            FROM sources s
            JOIN wiki_source_links wsl ON s.id = wsl.source_id
            JOIN wiki_pages wp ON wsl.wiki_page_id = wp.id
            WHERE wp.slug = ?
            ORDER BY s.ingested_at DESC
        """, (slug,))
        sources = await cursor.fetchall()

    return {
        "slug": page[0], "title": page[1], "content": page[2],
        "source_count": page[3], "last_compiled_at": page[4], "stale": bool(page[5]),
        "sources": [
            {"id": s[0], "title": s[1], "url": s[2], "summary": s[3], "ingested_at": s[4]}
            for s in sources
        ]
    }


@app.post("/wiki/{slug}/compile")
async def compile_wiki_page(slug: str):
    """Force recompile a wiki page."""
    from app.compile.compiler import compile_topic
    result = await compile_topic(slug)
    return result


@app.post("/compile/nightly")
async def nightly_compile():
    """Compile all stale wiki pages. Called by nightly cron."""
    from app.compile.compiler import compile_nightly
    result = await compile_nightly()
    return result


# --- Source CRUD Endpoints ---

@app.get("/sources")
async def list_sources(type: str | None = None, limit: int = 50, offset: int = 0):
    """Browse sources with optional type filter."""
    async with get_db() as db:
        if type:
            cursor = await db.execute(
                "SELECT id, url, source_type, title, author, ingested_at, enrichment_status FROM sources WHERE source_type = ? ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
                (type, limit, offset)
            )
        else:
            cursor = await db.execute(
                "SELECT id, url, source_type, title, author, ingested_at, enrichment_status FROM sources ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
        rows = await cursor.fetchall()

    return [
        {"id": r[0], "url": r[1], "source_type": r[2], "title": r[3],
         "author": r[4], "ingested_at": r[5], "enrichment_status": r[6]}
        for r in rows
    ]


@app.get("/sources/{source_id}")
async def get_source(source_id: str):
    """Full source detail + entities."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        source = await cursor.fetchone()

        if not source:
            return JSONResponse(status_code=404, content={"error": "Not found", "detail": "Source not found"})

        # Get column names from cursor description
        columns = [d[0] for d in cursor.description]
        source_dict = dict(zip(columns, source))

        # Get entities
        cursor = await db.execute(
            "SELECT entity_type, entity_name FROM entities WHERE source_id = ?", (source_id,)
        )
        entities = [{"type": e[0], "name": e[1]} for e in await cursor.fetchall()]

    source_dict["entities"] = entities
    return source_dict


@app.patch("/sources/{source_id}")
async def update_source(source_id: str, request: Request):
    """Edit user_context, attribution, summary, key_insights."""
    body = await request.json()
    allowed = {"user_context", "attribution", "summary", "key_insights"}
    updates = {k: v for k, v in body.items() if k in allowed}

    if not updates:
        return JSONResponse(status_code=422, content={"error": "No valid fields", "detail": f"Allowed: {allowed}"})

    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM sources WHERE id = ?", (source_id,))
        if not await cursor.fetchone():
            return JSONResponse(status_code=404, content={"error": "Not found", "detail": "Source not found"})

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        await db.execute(
            f"UPDATE sources SET {set_clause} WHERE id = ?",
            (*updates.values(), source_id)
        )
        await db.commit()

    return {"id": source_id, "updated": list(updates.keys())}


@app.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    """Cascade delete a source."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM sources WHERE id = ?", (source_id,))
        if not await cursor.fetchone():
            return JSONResponse(status_code=404, content={"error": "Not found"})

        await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        await db.commit()

    from app.enrichment.pipeline import regenerate_index
    await regenerate_index()

    return {"id": source_id, "deleted": True}


@app.post("/sources/{source_id}/re-enrich")
async def re_enrich_source(source_id: str, background_tasks: BackgroundTasks):
    """Re-run enrichment pipeline for a source."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM sources WHERE id = ?", (source_id,))
        if not await cursor.fetchone():
            return JSONResponse(status_code=404, content={"error": "Not found"})

        await db.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        await db.execute("DELETE FROM entities WHERE source_id = ?", (source_id,))
        await db.execute("DELETE FROM enrichment_jobs WHERE source_id = ?", (source_id,))
        await db.execute(
            "UPDATE sources SET enrichment_status = 'pending', summary = NULL, key_insights = NULL, topics = NULL WHERE id = ?",
            (source_id,)
        )
        await db.commit()

    from app.enrichment.pipeline import run_enrichment
    background_tasks.add_task(run_enrichment, source_id)
    return {"id": source_id, "status": "re-enrichment queued"}


@app.post("/sources/re-enrich-all")
async def re_enrich_all(background_tasks: BackgroundTasks):
    """Batch re-enrich all sources."""
    from app.enrichment.pipeline import run_enrichment

    async with get_db() as db:
        cursor = await db.execute("SELECT id FROM sources")
        sources = await cursor.fetchall()

    for row in sources:
        source_id = row[0]
        async with get_db() as db:
            await db.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
            await db.execute("DELETE FROM entities WHERE source_id = ?", (source_id,))
            await db.execute("DELETE FROM enrichment_jobs WHERE source_id = ?", (source_id,))
            await db.execute(
                "UPDATE sources SET enrichment_status = 'pending', summary = NULL, key_insights = NULL, topics = NULL WHERE id = ?",
                (source_id,)
            )
            await db.commit()
        background_tasks.add_task(run_enrichment, source_id)

    return {"queued": len(sources)}


@app.get("/stats", response_model=StatsResponse)
async def stats():
    """Database statistics."""
    async with get_db() as db:
        # Total sources
        cursor = await db.execute("SELECT COUNT(*) FROM sources")
        total_sources = (await cursor.fetchone())[0]

        # By type
        cursor = await db.execute(
            "SELECT source_type, COUNT(*) FROM sources GROUP BY source_type"
        )
        rows = await cursor.fetchall()
        by_type = {row[0]: row[1] for row in rows}

        # Total chunks
        cursor = await db.execute("SELECT COUNT(*) FROM chunks")
        total_chunks = (await cursor.fetchone())[0]

        # Total wiki pages
        cursor = await db.execute("SELECT COUNT(*) FROM wiki_pages")
        total_wiki_pages = (await cursor.fetchone())[0]

        # Stale topics
        cursor = await db.execute("SELECT COUNT(*) FROM wiki_pages WHERE stale = 1")
        stale_topics = (await cursor.fetchone())[0]

    return StatsResponse(
        total_sources=total_sources,
        by_type=by_type,
        total_chunks=total_chunks,
        total_wiki_pages=total_wiki_pages,
        stale_topics=stale_topics,
    )
