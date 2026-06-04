import hashlib
import json
import os
import time
import threading
from calendar import monthrange
from datetime import datetime

import requests
import chromadb
from sentence_transformers import SentenceTransformer

from chunking import chunk_documents
import bm25_index as _bm25_mod

_BASE           = os.path.dirname(os.path.abspath(__file__))
BASE_URL        = "https://denver.co.civic.band/meetings/minutes.json"
CHROMA_DIR      = os.path.join(_BASE, "data", "chroma")
MANIFEST_PATH   = os.path.join(_BASE, "data", "index_manifest.json")
COLLECTION_NAME = "denver_all"
MODEL_NAME      = "sentence-transformers/all-MiniLM-L6-v2"

BATCH_SIZE = 256

# ── Thread-safe progress state ────────────────────────────────
_state = {"status": "idle", "lines": [], "collection_name": None}
_lock  = threading.Lock()


def get_progress():
    with _lock:
        return {
            "status":          _state["status"],
            "lines":           list(_state["lines"]),
            "collection_name": _state["collection_name"],
        }


def reset():
    with _lock:
        _state.update(status="idle", lines=[], collection_name=None)


def _log(msg):
    with _lock:
        lines = _state["lines"]
        # Update last line in-place for repeated progress lines
        if lines and (
            (msg.startswith("Creating embeddings") and lines[-1].startswith("Creating embeddings")) or
            ("rows so far" in msg and "rows so far" in lines[-1])
        ):
            lines[-1] = msg
        else:
            lines.append(msg)


def _set(**kw):
    with _lock:
        _state.update(kw)


# ── Manifest helpers ─────────────────────────────────────────

def _load_manifest():
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {"segments": [], "total_chunks": 0}


def _save_manifest(manifest):
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


# ── Coverage / collection helpers ────────────────────────────

def get_active_collection_name():
    """Returns the best available collection name, or None."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    for name in [COLLECTION_NAME, "denver_council_2025"]:
        try:
            if client.get_collection(name).count() > 0:
                return name
        except Exception:
            pass
    return None


def get_coverage():
    """
    Returns a dict describing what is currently indexed:
      {
        "total_chunks": int,
        "segments": [{"label": str, "chunks": int}, ...]
      }
    """
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    # Prefer denver_all + manifest
    try:
        col   = client.get_collection(COLLECTION_NAME)
        count = col.count()
        if count > 0:
            manifest = _load_manifest()
            manifest["total_chunks"] = count
            return manifest
    except Exception:
        pass

    # Fall back to legacy pre-built collection
    try:
        col   = client.get_collection("denver_council_2025")
        count = col.count()
        if count > 0:
            return {
                "total_chunks": count,
                "segments": [{
                    "label":         "CityCouncil · Jan–Dec 2025 (pre-indexed)",
                    "meeting_types": ["CityCouncil"],
                    "date_from":     "2025-01",
                    "date_to":       "2025-12",
                    "chunks":        count,
                }],
            }
    except Exception:
        pass

    return {"total_chunks": 0, "segments": []}


# ── Internal helpers ─────────────────────────────────────────

def _month_bounds(yyyy_mm_from, yyyy_mm_to):
    y2, m2 = map(int, yyyy_mm_to.split("-"))
    _, last = monthrange(y2, m2)
    return f"{yyyy_mm_from}-01", f"{yyyy_mm_to}-{last:02d}"


def _fetch_one(meeting_type, date_from, date_to):
    rows, cursor = [], None
    while True:
        params = {
            "meeting":    meeting_type,
            "date__gte":  date_from,
            "date__lte":  date_to,
            "_size":      100,
            "_sort_desc": "date",
        }
        if cursor:
            params["_next"] = cursor
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            if resp.status_code != 200:
                _log(f"  API {resp.status_code} for {meeting_type}")
                break
        except requests.RequestException as e:
            _log(f"  Network error: {e}")
            break

        data  = resp.json()
        batch = data.get("rows", [])
        if not batch:
            break
        rows.extend(batch)
        _log(f"  Fetching {meeting_type}... ({len(rows)} rows so far)")
        cursor = data.get("next")
        if not cursor:
            break
        time.sleep(0.3)
    return rows


# ── Pipeline ─────────────────────────────────────────────────

def _run(meeting_types, date_from, date_to):
    _set(status="running", lines=[], collection_name=None)
    date_from_full, date_to_full = _month_bounds(date_from, date_to)

    try:
        # ── Fetch ──────────────────────────────────────────
        all_rows = []
        for mt in meeting_types:
            _log(f"Fetching {mt}...")
            rows = _fetch_one(mt, date_from_full, date_to_full)
            _log(f"  ✓ {mt}: {len(rows)} rows")
            all_rows.extend(rows)

        _log(f"Total: {len(all_rows)} rows fetched")
        if not all_rows:
            _log("No data found for this date range / meeting types.")
            _set(status="error")
            return

        # ── Chunk (resolution-boundary aware) ──────────────
        _log("Chunking text...")
        chunk_pairs = chunk_documents(all_rows)
        chunks    = [c for c, _ in chunk_pairs]
        metadatas = [m for _, m in chunk_pairs]
        # Deterministic IDs: meeting+date+page+chunk_index → no duplicates on re-index
        ids = [
            hashlib.md5(
                f"{m['meeting']}_{m['date']}_{m['page']}_{m['chunk_index']}".encode()
            ).hexdigest()[:16]
            for m in metadatas
        ]
        _log(f"  {len(chunks)} chunks created")

        # ── Embed & upsert ─────────────────────────────────
        _log("Loading embedding model...")
        model = SentenceTransformer(MODEL_NAME)

        client = chromadb.PersistentClient(path=CHROMA_DIR)
        try:
            collection = client.get_collection(COLLECTION_NAME)
        except Exception:
            collection = client.create_collection(COLLECTION_NAME)

        _log(f"Creating embeddings... (0/{len(chunks)} chunks)")
        for i in range(0, len(chunks), BATCH_SIZE):
            batch_texts = chunks[i:i + BATCH_SIZE]
            batch_meta  = metadatas[i:i + BATCH_SIZE]
            batch_ids   = ids[i:i + BATCH_SIZE]
            embeddings  = model.encode(batch_texts, show_progress_bar=False).tolist()
            collection.upsert(
                ids=batch_ids,
                embeddings=embeddings,
                documents=batch_texts,
                metadatas=batch_meta,
            )
            _log(f"Creating embeddings... ({min(i + BATCH_SIZE, len(chunks))}/{len(chunks)} chunks)")

        # ── Build keyword (BM25) index ─────────────────────
        _log("Building keyword index...")
        _bm25_mod.rebuild_from_chromadb(collection)
        _log(f"  ✓ Keyword index built ({collection.count():,} chunks)")

        # ── Update manifest ────────────────────────────────
        total   = collection.count()
        n_types = len(meeting_types)
        type_label = (
            meeting_types[0] if n_types == 1
            else f"{meeting_types[0]} +{n_types - 1} more"
        )
        manifest = _load_manifest()
        manifest["segments"].append({
            "label":         f"{type_label} · {date_from}–{date_to}",
            "meeting_types": meeting_types,
            "date_from":     date_from,
            "date_to":       date_to,
            "chunks":        len(chunks),
            "bm25_built":    True,
            "bm25_chunks":   total,
            "indexed_at":    datetime.utcnow().isoformat(),
        })
        manifest["total_chunks"] = total
        _save_manifest(manifest)

        # Refresh BM25 in the live query engine
        try:
            from query import refresh_bm25
            refresh_bm25()
        except Exception:
            pass

        _log(f"✓ Ready! {total:,} chunks indexed with hybrid search.")
        _set(status="done", collection_name=COLLECTION_NAME)

    except Exception as e:
        _log(f"Error: {e}")
        _set(status="error")


def start_pipeline(meeting_types, date_from, date_to):
    threading.Thread(
        target=_run,
        args=(meeting_types, date_from, date_to),
        daemon=True,
    ).start()
