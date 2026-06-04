import hashlib
import os
import time
import threading
from calendar import monthrange

import requests
import chromadb
from sentence_transformers import SentenceTransformer

_BASE      = os.path.dirname(os.path.abspath(__file__))
BASE_URL   = "https://denver.co.civic.band/meetings/minutes.json"
CHROMA_DIR = os.path.join(_BASE, "data", "chroma")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

CHUNK_SIZE    = 512   # chars, matches embed.py
CHUNK_OVERLAP = 50
BATCH_SIZE    = 256

# ── Thread-safe progress state ───────────────────────────────
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
        # Update last line in-place for repeated progress lines (avoids visual noise)
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


# ── Collection helpers ────────────────────────────────────────

def collection_name_for(meeting_types: list, date_from: str, date_to: str) -> str:
    h = hashlib.md5(",".join(sorted(meeting_types)).encode()).hexdigest()[:8]
    return f"denver_{h}_{date_from}_{date_to}"


def collection_exists(name: str) -> int:
    """Returns chunk count if the collection exists, 0 otherwise."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        return client.get_collection(name).count()
    except Exception:
        return 0


def get_latest_collection() -> tuple:
    """Returns (name, chunk_count) for the first available collection, or (None, 0)."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        cols = [c for c in client.list_collections() if c.name.startswith("denver_")]
        if cols:
            return cols[0].name, cols[0].count()
    except Exception:
        pass
    return None, 0


# ── Internal helpers ─────────────────────────────────────────

def _month_bounds(yyyy_mm_from: str, yyyy_mm_to: str) -> tuple:
    y2, m2 = map(int, yyyy_mm_to.split("-"))
    _, last = monthrange(y2, m2)
    return f"{yyyy_mm_from}-01", f"{yyyy_mm_to}-{last:02d}"


def _chunk_text(text: str) -> list:
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_SIZE
        if end < len(text):
            last_space = text.rfind(" ", start, end)
            if last_space > start:
                end = last_space
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - CHUNK_OVERLAP
    return chunks


def _fetch_one(meeting_type: str, date_from: str, date_to: str) -> list:
    rows, cursor = [], None
    while True:
        params = {
            "meeting":   meeting_type,
            "date__gte": date_from,
            "date__lte": date_to,
            "_size":     100,
            "_sort_desc": "date",
        }
        if cursor:
            params["_next"] = cursor
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            if resp.status_code != 200:
                _log(f"  API error {resp.status_code} for {meeting_type}")
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

def _run(meeting_types: list, date_from: str, date_to: str, coll_name: str):
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

        # ── Chunk ──────────────────────────────────────────
        _log("Chunking text...")
        chunks, metadatas = [], []
        for row in all_rows:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            for c in _chunk_text(text):
                chunks.append(c)
                metadatas.append({
                    "date":    row.get("date", ""),
                    "meeting": row.get("meeting", ""),
                    "page":    str(row.get("page", "")),
                })
        _log(f"  {len(chunks)} chunks created")

        # ── Embed & store ───────────────────────────────────
        _log("Loading embedding model...")
        model = SentenceTransformer(MODEL_NAME)

        client = chromadb.PersistentClient(path=CHROMA_DIR)
        try:
            client.delete_collection(coll_name)
        except Exception:
            pass
        collection = client.create_collection(coll_name)

        _log(f"Creating embeddings... (0/{len(chunks)} chunks)")
        for i in range(0, len(chunks), BATCH_SIZE):
            batch_texts = chunks[i:i + BATCH_SIZE]
            batch_meta  = metadatas[i:i + BATCH_SIZE]
            embeddings  = model.encode(batch_texts, show_progress_bar=False).tolist()
            collection.add(
                ids=[f"{coll_name}_{i + j}" for j in range(len(batch_texts))],
                embeddings=embeddings,
                documents=batch_texts,
                metadatas=batch_meta,
            )
            _log(f"Creating embeddings... ({min(i + BATCH_SIZE, len(chunks))}/{len(chunks)} chunks)")

        _log(f"✓ Ready! {len(chunks)} chunks indexed.")
        _set(status="done", collection_name=coll_name)

    except Exception as e:
        _log(f"Error: {e}")
        _set(status="error")


def start_pipeline(meeting_types: list, date_from: str, date_to: str) -> str:
    """Start ingestion in a background thread. Returns the target collection name."""
    coll_name = collection_name_for(meeting_types, date_from, date_to)
    threading.Thread(
        target=_run,
        args=(meeting_types, date_from, date_to, coll_name),
        daemon=True,
    ).start()
    return coll_name
