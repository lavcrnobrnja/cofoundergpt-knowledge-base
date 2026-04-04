"""Knowledge Base v2 — FastAPI application."""
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from fastapi.responses import JSONResponse as _JSONResponse

from app.config import DB_PATH
from app.database import init_db, get_db
from app.models import HealthResponse, StatsResponse, IngestRequest, IngestResponse


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
