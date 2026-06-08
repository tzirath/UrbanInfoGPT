# UrbanInfoGPT — Project Context

## What this is
A RAG system that indexes Denver City Council meeting minutes and lets users query them via a dashboard. Combines vector search (ChromaDB) with keyword search (BM25).

## Key files
- `pipeline.py` — fetch → chunk → embed → upsert → build BM25 → update manifest. `run_pipeline()` is synchronous; `start_pipeline()` is async (threaded).
- `chunking.py` — splits meeting minutes by resolution boundary (regex `RESOLUTION_PATTERN`). Long resolutions are sentence-split. Short procedural text uses word-boundary chunks.
- `bm25_index.py` — BM25 keyword index; rebuilt from the full ChromaDB collection after every quarter is indexed.
- `query.py` — hybrid retrieval; exposes `refresh_bm25()`.
- `scripts/index_history.py` — indexes history quarter by quarter to avoid OOM. Safe to interrupt and resume (checks manifest before each quarter).
- `scripts/run_analytics.py` — regenerates `data/analytics/` JSON files.
- `dashboard.py` — Dash web app.

## Data
- ChromaDB collection: `denver_all` (all years), legacy: `denver_council_2025`.
- Manifest: `data/index_manifest.json` — tracks which quarters are indexed and chunk counts.
- Analytics JSON: `data/analytics/financials.json`, `data/analytics/votes.json`.
- Embedding model: `sentence-transformers/all-MiniLM-L6-v2`.

## Indexing
- Years in scope: 2022–2026 (`YEARS_TO_INDEX` in `index_history.py`).
- Meeting type: `CityCouncil`.
- Indexed quarter by quarter (full-year indexing caused OOM kill).
- Manifest prevents re-indexing already-done quarters.

## Chunking design
- Resolution blocks kept whole if ≤ 800 chars; split at sentence boundaries with 100-char overlap if longer.
- Procedural/non-resolution text split into ~300-char word-boundary chunks with 50-char overlap.
- Continuation chunks get resolution number prepended: `[Resolution 25-0545] ...`.
- `bisect` used in `_split_at_sentences` for O(log n) sentence-boundary lookup (was O(n²)).

## Gotchas
- `TOKENIZERS_PARALLELISM=false` must be set before importing sentence-transformers on macOS Python 3.9 (semaphore leak).
- BM25 index is rebuilt from the **entire** collection after each quarter — gets slower as total chunk count grows.
- Chunk IDs are MD5(`meeting_date_page_chunkIndex`)[:16] — deterministic, safe to re-upsert.
- `data/analytics/*.json` are generated files; don't commit changes to them unless intentional.
