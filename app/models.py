"""Pydantic request/response models."""
from typing import Optional
from pydantic import BaseModel


# --- Ingest ---

class IngestRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    user_context: Optional[str] = None
    attribution: Optional[str] = None
    source_type: Optional[str] = None


class IngestResponse(BaseModel):
    id: str
    title: Optional[str] = None
    source_type: str
    status: str


# --- Query ---

class QueryRequest(BaseModel):
    query: str


class SourceInfo(BaseModel):
    id: str
    title: Optional[str] = None
    url: Optional[str] = None
    relevance: Optional[float] = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceInfo] = []
    wiki_pages: list[str] = []
    related_topics: list[str] = []


# --- Sources ---

class SourceResponse(BaseModel):
    id: str
    url: Optional[str] = None
    source_type: str
    title: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    ingested_at: str
    summary: Optional[str] = None
    key_insights: Optional[str] = None
    topics: Optional[str] = None
    user_context: Optional[str] = None
    attribution: Optional[str] = None
    enrichment_status: str


class SourceListResponse(BaseModel):
    sources: list[SourceResponse]
    total: int


class SourcePatchRequest(BaseModel):
    user_context: Optional[str] = None
    attribution: Optional[str] = None
    summary: Optional[str] = None
    key_insights: Optional[str] = None


# --- Pipeline ---

class PipelineStageResponse(BaseModel):
    stage: str
    status: str
    attempt: int
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


# --- Wiki ---

class WikiPageResponse(BaseModel):
    id: str
    slug: str
    title: str
    content: Optional[str] = None
    source_count: int
    stale: bool
    created_at: str
    last_compiled_at: Optional[str] = None


class WikiListResponse(BaseModel):
    pages: list[WikiPageResponse]


# --- System ---

class StatsResponse(BaseModel):
    total_sources: int
    by_type: dict
    total_chunks: int
    total_wiki_pages: int
    stale_topics: int


class HealthResponse(BaseModel):
    status: str
    db_size_mb: float
    uptime_seconds: float


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
