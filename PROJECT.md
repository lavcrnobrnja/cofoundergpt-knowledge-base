# Knowledge Base v2

Personal knowledge management service ("Second Brain") for Lav.

## Stack
Python 3.13 + FastAPI + SQLite WAL + google-genai + PyMuPDF

## Port
8555 (localhost only)

## Deployment
- **Service:** launchd `com.cofoundergpt.knowledge-base` (KeepAlive, RunAtLoad)
- **Plist:** `~/Library/LaunchAgents/com.cofoundergpt.knowledge-base.plist`
- **Logs:** `data/kb.log`, `data/kb-error.log`
- **Nightly cron:** `kb-nightly-compile` (3am ET) — compiles stale wiki topics, delivers to Telegram

## Key Files
- `app/main.py` — FastAPI app, all 15 endpoints + dashboard static mount
- `app/database.py` — SQLite schema (6 tables, 9 indexes, WAL mode)
- `app/config.py` — API keys, model names, paths
- `app/models.py` — Pydantic request/response schemas
- `app/ingest/` — Source type detection + 5 extractors (article, youtube, tweet, quote, pdf)
- `app/enrichment/pipeline.py` — 4-stage pipeline (metadata→summary→extraction→vectors)
- `app/enrichment/prompts.py` — LLM prompts for enrichment
- `app/embedding.py` — Gemini Embedding 2 (chunk, embed, cosine similarity)
- `app/search.py` — Vector search + wiki search (empty-DB safe)
- `app/synthesis.py` — Context assembly + Gemini Pro synthesis
- `app/compile/compiler.py` — Wiki page compilation via Gemini Pro
- `app/compile/prompts.py` — Compilation prompt template
- `static/index.html` — Dashboard (dark theme, CofounderGPT branding)

## How to Run
```
source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8555
```

## How to Test
```
source .venv/bin/activate && pytest -v
```

## API Endpoints (15)
- POST /ingest — ingest URL, file path, or text (triggers background enrichment)
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

## Source Types
article, youtube, tweet, substack, quote, voice_memo, pdf

## Current State (Apr 4, 2026)
73 tests passing. Service live. All endpoints verified. Dashboard live at http://127.0.0.1:8555/.

**BLOCKER:** Gemini API key (G7fw) revoked by Google (leaked key detection). Enrichment stages 2-4 (summary, extraction, vectors) will fail until a new key is provisioned. Ingest + metadata extraction works. Queries on empty KB return graceful response.

## Telegram Integration
Handled by CofounderGPT main session:
- `kb:` prefix → POST /ingest
- `kb?` prefix → POST /query

## DO NOT MODIFY
- Schema (without migration)
- Config model names (spec-locked)
