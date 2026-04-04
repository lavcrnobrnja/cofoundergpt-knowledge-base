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
Step 4 complete — Vector search + wiki search + query synthesis. POST /query endpoint accepts a question and returns a synthesized answer from Gemini Pro with cited sources, relevant wiki pages, and related topics. Search uses cosine similarity with 30-day half-life time boost and per-source deduplication. 41 tests passing.

Previous: Step 3 — 4-stage enrichment pipeline (metadata, summary, extraction, vectors) with Gemini Flash LLM and embedding + similarity gate for topics.

## Key Files (Search/Query)
- `app/search.py` — vector_search (chunks) + wiki_search (pages), time-boosted ranking
- `app/synthesis.py` — context assembly + Gemini Pro synthesis
- `tests/test_search.py` — 9 tests covering search, dedup, time boost, synthesis, endpoint

## DO NOT MODIFY
- Schema (without migration)
- Config model names (spec-locked)
