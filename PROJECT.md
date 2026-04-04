# Knowledge Base v2

Personal knowledge management service ("Second Brain") for Lav.

## Stack
Python 3.13 + FastAPI + SQLite WAL + google-genai

## Port
8555

## Key Files
- `app/main.py` — FastAPI app, /health, /stats endpoints
- `app/database.py` — SQLite schema (6 tables, 9 indexes)
- `app/config.py` — API keys, model names, paths
- `app/models.py` — Pydantic request/response schemas

## How to Run
```
source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8555
```

## How to Test
```
source .venv/bin/activate && pytest -v
```

## Current State
Step 3 complete — 4-stage enrichment pipeline (metadata, summary, extraction, vectors) with Gemini Flash LLM and embedding + similarity gate for topics. Asynchronous trigger via FastAPI BackgroundTasks. 32 tests passing.

Previous: Step 2 — POST /ingest with article, YouTube, tweet, quote extractors. Dedup (same content → 200, different content → re-ingest).

## DO NOT MODIFY
- Schema (without migration)
- Config model names (spec-locked)
