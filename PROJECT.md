# Knowledge Base v2

Personal knowledge management service ("Second Brain") for Lav.

## Stack
Python 3.13 + FastAPI + SQLite WAL + google-genai

## Port
8555 (localhost only)

## Deployment
- **Service:** launchd `com.cofoundergpt.knowledge-base` (KeepAlive, RunAtLoad)
- **Plist:** `~/Library/LaunchAgents/com.cofoundergpt.knowledge-base.plist`
- **Logs:** `data/kb.log`, `data/kb-error.log`
- **Nightly cron:** `kb-nightly-compile` (3am ET) — compiles stale wiki topics

## Key Files
- `app/main.py` — FastAPI app, all endpoints
- `app/database.py` — SQLite schema (6 tables, 9 indexes)
- `app/config.py` — API keys, model names, paths
- `app/models.py` — Pydantic request/response schemas
- `app/ingest/` — Source type detection + 4 extractors (article, youtube, tweet, quote)
- `app/enrichment/pipeline.py` — 4-stage pipeline (metadata→summary→extraction→vectors)
- `app/enrichment/prompts.py` — LLM prompts for enrichment
- `app/embedding.py` — Gemini Embedding 2 (chunk, embed, cosine similarity)
- `app/search.py` — Vector search + wiki search
- `app/synthesis.py` — Context assembly + Gemini Pro synthesis
- `app/compile/compiler.py` — Wiki page compilation via Gemini Pro
- `app/compile/prompts.py` — Compilation prompt template

## How to Run
```
source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8555
```

## How to Test
```
source .venv/bin/activate && pytest -v
```

## API Endpoints
- POST /ingest — ingest URL or text (triggers background enrichment)
- POST /query — ask a question, get synthesized answer
- GET /sources — list sources (optional ?type= filter)
- GET /sources/{id} — source detail + entities
- PATCH /sources/{id} — edit user_context, attribution, summary, key_insights
- DELETE /sources/{id} — cascade delete
- POST /sources/{id}/re-enrich — re-run enrichment
- POST /sources/re-enrich-all — batch re-enrich
- GET /sources/{id}/pipeline — enrichment stage status
- GET /wiki — list wiki pages
- GET /wiki/{slug} — wiki page detail + linked sources
- POST /wiki/{slug}/compile — force recompile
- POST /compile/nightly — compile all stale topics
- GET /health — health check
- GET /stats — database statistics

## Current State
All 6 steps complete. 58 tests passing. Service running on port 8555 via launchd. Nightly compile cron at 3am ET.

## Telegram Integration
Handled by CofounderGPT main session:
- `kb:` prefix → POST /ingest
- `kb?` prefix → POST /query

## DO NOT MODIFY
- Schema (without migration)
- Config model names (spec-locked)
