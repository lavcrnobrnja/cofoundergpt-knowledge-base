"""Database initialization and connection management."""
import aiosqlite
from contextlib import asynccontextmanager
from app import config


SCHEMA_SQL = """
-- SOURCES — one row per ingested item
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    url             TEXT UNIQUE,
    source_type     TEXT NOT NULL,
    title           TEXT,
    author          TEXT,
    published_at    TEXT,
    ingested_at     TEXT NOT NULL DEFAULT (datetime('now')),
    content_hash    TEXT NOT NULL,
    raw_content     TEXT,
    metadata        TEXT,
    user_context    TEXT,
    attribution     TEXT,
    summary         TEXT,
    key_insights    TEXT,
    topics          TEXT,
    enrichment_status TEXT NOT NULL DEFAULT 'pending'
);

-- CHUNKS — for vector search
CREATE TABLE IF NOT EXISTS chunks (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    embedding       BLOB,
    token_count     INTEGER
);

-- ENTITIES — extracted people, companies, concepts
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    entity_type     TEXT NOT NULL,
    entity_name     TEXT NOT NULL,
    UNIQUE(source_id, entity_type, entity_name)
);

-- ENRICHMENT_JOBS — per-stage pipeline tracking
CREATE TABLE IF NOT EXISTS enrichment_jobs (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempt         INTEGER NOT NULL DEFAULT 0,
    result          TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

-- WIKI_PAGES — the compile layer
CREATE TABLE IF NOT EXISTS wiki_pages (
    id              TEXT PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT,
    source_count    INTEGER NOT NULL DEFAULT 0,
    embedding       BLOB,
    stale           INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_compiled_at TEXT
);

-- WIKI_SOURCE_LINKS — which sources feed which wiki page
CREATE TABLE IF NOT EXISTS wiki_source_links (
    wiki_page_id    TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    PRIMARY KEY (wiki_page_id, source_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_entities_source ON entities(source_id);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(entity_name);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON enrichment_jobs(source_id);
CREATE INDEX IF NOT EXISTS idx_wiki_links_source ON wiki_source_links(source_id);
CREATE INDEX IF NOT EXISTS idx_sources_type ON sources(source_type);
CREATE INDEX IF NOT EXISTS idx_sources_ingested ON sources(ingested_at);
CREATE INDEX IF NOT EXISTS idx_wiki_stale ON wiki_pages(stale) WHERE stale = 1;
"""


async def init_db():
    """Initialize the database: create tables and indexes if they don't exist."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(config.DB_PATH)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(SCHEMA_SQL)
        await db.commit()


@asynccontextmanager
async def get_db():
    """Async context manager for database connections."""
    db = await aiosqlite.connect(str(config.DB_PATH))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
