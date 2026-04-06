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
article, youtube, tweet, quote, voice_memo, pdf

(substack URLs auto-detected as article)

## Current State (Apr 5, 2026)
Service live. Dashboard at http://127.0.0.1:8555/ defaults to Sources tab. **38 sources** (20 tweets, 13 YouTube, 4 articles, 1 GitHub).

- Gemini key configured and working. Enrichment pipeline fully functional.
- Native Twitter ingest via `xurl`. Three tweet types handled:
  - **Regular tweets:** text from API
  - **X Articles:** title from API + full body via Jina Reader (`r.jina.ai`)
  - **Video tweets:** detected via X API media expansion, audio downloaded via yt-dlp, transcribed via Whisper. Metadata flags: `has_video`, `video_transcribed`. 50MB/120s/300s guards.
  - **Threads:** manual workaround via ThreadReaderApp (no auto-thread detection yet)
- Author resolution uses `includes.users` from xurl response (not raw numeric `author_id`).
- `guests` extraction restricted to interviews/podcasts/panels only.
- Dashboard shows author on source list cards + detail popup. @ prefix for tweet authors.
- Substack URLs auto-merge into article type.
- Nightly compile cron (5da29338) active, runs 3am ET on Flash Lite.

## Telegram Integration
Handled by CofounderGPT main session:
- `kb:` prefix → POST /ingest
- `kb?` prefix → POST /query

## DO NOT MODIFY
- Schema (without migration)
- Config model names (spec-locked)
