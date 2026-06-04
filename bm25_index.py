import os
import pickle
import re
from calendar import monthrange
from typing import Dict, List, Optional, Tuple

_BASE     = os.path.dirname(os.path.abspath(__file__))
BM25_DIR  = os.path.join(_BASE, "data", "bm25")
_IDX_PATH = os.path.join(BM25_DIR, "index.pkl")
_CHK_PATH = os.path.join(BM25_DIR, "chunks.pkl")
_MET_PATH = os.path.join(BM25_DIR, "metadata.pkl")


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokenisation — preserves resolution numbers."""
    return re.findall(r'[a-zA-Z0-9]+', text.lower())


# ── Build / load ─────────────────────────────────────────────

def build_bm25_index(chunks: List[str], metadatas: List[Dict]):
    """Build BM25 index from chunk texts and save all three pkl files."""
    from rank_bm25 import BM25Okapi
    os.makedirs(BM25_DIR, exist_ok=True)
    bm25 = BM25Okapi([_tokenize(c) for c in chunks])
    with open(_IDX_PATH, "wb") as f: pickle.dump(bm25,              f)
    with open(_CHK_PATH, "wb") as f: pickle.dump(list(chunks),      f)
    with open(_MET_PATH, "wb") as f: pickle.dump(list(metadatas),   f)
    return bm25


def load_bm25_index() -> Tuple[Optional[object], Optional[List], Optional[List]]:
    """Load BM25 index from disk. Returns (None, None, None) if absent."""
    if not all(os.path.exists(p) for p in [_IDX_PATH, _CHK_PATH, _MET_PATH]):
        return None, None, None
    try:
        with open(_IDX_PATH, "rb") as f: bm25      = pickle.load(f)
        with open(_CHK_PATH, "rb") as f: chunks    = pickle.load(f)
        with open(_MET_PATH, "rb") as f: metadatas = pickle.load(f)
        return bm25, chunks, metadatas
    except Exception:
        return None, None, None


def rebuild_from_chromadb(collection) -> Optional[object]:
    """
    Export every document from a ChromaDB collection and rebuild the
    BM25 index from scratch. Called after each successful pipeline run
    so BM25 always reflects the full collection.
    """
    data = collection.get(include=["documents", "metadatas"])
    docs = data.get("documents") or []
    if not docs:
        return None
    return build_bm25_index(docs, data["metadatas"])


# ── Search ───────────────────────────────────────────────────

def bm25_search(
    question: str,
    bm25,
    chunks: List[str],
    metadatas: List[Dict],
    n_results: int = 20,
    date_from: str = None,
    date_to: str   = None,
    meeting_types:  List[str] = None,
    content_type:   str       = None,
) -> List[Dict]:
    """
    Keyword search using a pre-loaded BM25 index.
    Metadata filters are applied in Python after scoring.
    content_type filter is skipped for chunks that lack the field
    (backward-compatible with legacy collections).
    """
    tokens = _tokenize(question)
    scores = bm25.get_scores(tokens)
    max_s  = float(max(scores)) if len(scores) > 0 and max(scores) > 0 else 1.0

    lo = f"{date_from}-01" if date_from else None
    hi = None
    if date_to:
        y, m = map(int, date_to.split("-"))
        _, last = monthrange(y, m)
        hi = f"{date_to}-{last:02d}"

    ranked  = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    results = []

    for idx in ranked:
        if len(results) >= n_results:
            break
        meta  = metadatas[idx]
        score = scores[idx]
        d     = meta.get("date", "")

        if lo and d < lo:
            continue
        if hi and d > hi:
            continue
        if meeting_types and meta.get("meeting") not in meeting_types:
            continue
        # Only filter by content_type when the field is present in the metadata
        if content_type and meta.get("content_type"):
            if meta["content_type"] != content_type:
                continue

        results.append({
            "text":              chunks[idx],
            "date":              d,
            "meeting":           meta.get("meeting", ""),
            "page":              meta.get("page", ""),
            "resolution_number": meta.get("resolution_number") or None,
            "content_type":      meta.get("content_type", "other"),
            "bm25_score":        round(score / max_s, 3),
            "semantic_score":    0.0,
            "score":             round(score / max_s, 3),
        })

    return results
