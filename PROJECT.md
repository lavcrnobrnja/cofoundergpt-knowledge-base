# Knowledge Base v2

Personal knowledge management service ("Second Brain") for Lav.

## Stack
Python 3.13 + FastAPI + SQLite WAL + google-genai

## Port
8555

## Key Files
- `app/main.py` — FastAPI app, all endpoints (health, stats, ingest, query, wiki, sources CRUD)
- `app/database.py` — SQLite schema (6 tables, 9 indexes)
- `app/config.py` — API keys, model names, paths
- `app/models.py` — Pydantic request/response schemas
- `app/compile/compiler.py` — Wiki page compilation via Gemini Pro
- `app/compile/prompts.py` — Compilation prompt template
- `app/enrichment/pipeline.py` — 4-stage enrichment + regenerate_index()
- `app/search.py` — Vector search + wiki search
- `app/synthesis.py` — Context assembly + Gemini Pro synthesis

## How to Run
```
source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8555
```

## How to Test
```
source .venv/bin/activate && pytest -v
```

## Current State
Step 5 complete — Compile layer, wiki endpoints, and source CRUD. 58 tests passing.

- **Compile layer**: `compile_topic(slug)` compiles a wiki page from linked sources via Gemini Pro, saves to DB + disk, re-embeds, regenerates index. `compile_nightly()` compiles all stale topics. Detects SPLIT_SUGGESTED in LLM output.
- **Wiki endpoints**: GET /wiki (list), GET /wiki/{slug} (detail + linked sources), POST /wiki/{slug}/compile (force recompile), POST /compile/nightly (batch stale)
- **Source CRUD**: GET /sources (list + type filter), GET /sources/{id} (detail + entities), PATCH /sources/{id} (edit allowed fields), DELETE /sources/{id} (cascade + reindex), POST /sources/{id}/re-enrich, POST /sources/re-enrich-all

Previous: Step 4 — Vector search + wiki search + query synthesis (POST /query).

## DO NOT MODIFY
- Schema (without migration)
- Config model names (spec-locked)
