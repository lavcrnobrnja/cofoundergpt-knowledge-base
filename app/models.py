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

class SourcePatchRequest(BaseModel):
    user_context: Optional[str] = None
    attribution: Optional[str] = None
    summary: Optional[str] = None
    key_insights: Optional[str] = None


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
