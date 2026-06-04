import warnings
warnings.filterwarnings("ignore")

import re
from calendar import monthrange
from typing import Dict
import chromadb
from sentence_transformers import SentenceTransformer

import bm25_index as _bm25_mod

CHROMA_DIR      = "data/chroma"
COLLECTION_NAME = "denver_council_2025"   # legacy default
MODEL_NAME      = "sentence-transformers/all-MiniLM-L6-v2"
N_RESULTS       = 5
CANDIDATE_POOL  = 20   # candidates fetched from each source before merging

# ── Module-level singletons ──────────────────────────────────
print("Loading query engine...")
_model  = SentenceTransformer(MODEL_NAME)
_client = chromadb.PersistentClient(path=CHROMA_DIR)

try:
    _default_collection = _client.get_collection(COLLECTION_NAME)
    print(f"Ready. {_default_collection.count()} chunks indexed.")
except Exception:
    _default_collection = None
    print(f"Note: default collection '{COLLECTION_NAME}' not found — load data via the dashboard.")

# BM25 index — loaded at startup, refreshed after each pipeline run
_bm25        = None
_bm25_chunks = None
_bm25_meta   = None


def _load_bm25():
    global _bm25, _bm25_chunks, _bm25_meta
    b, c, m = _bm25_mod.load_bm25_index()
    if b is not None:
        _bm25, _bm25_chunks, _bm25_meta = b, c, m
        print(f"BM25 index loaded: {len(c):,} chunks")
    else:
        print("BM25 index not found — using semantic search only")


_load_bm25()


def refresh_bm25():
    """Reload BM25 index from disk. Called by pipeline after a successful run."""
    _load_bm25()


# ── Helpers ──────────────────────────────────────────────────

def _detect_weights(question: str) -> tuple:
    """
    Adaptive weight selection based on query characteristics.
    Returns (semantic_weight, bm25_weight).
    """
    if re.search(r'\d{2}-\d{4}', question):          # resolution number
        return 0.3, 0.7
    if re.search(r'\$[\d,]+', question):              # dollar amount
        return 0.3, 0.7
    procedural = ['resolution', 'ordinance', 'bill', 'vote',
                  'approved', 'adopted', 'passed']
    if any(t in question.lower() for t in procedural):
        return 0.4, 0.6
    return 0.7, 0.3                                   # conceptual question


def _build_where(meeting_types=None, content_type=None):
    """Build ChromaDB where clause for supported string filters."""
    conditions = []
    if meeting_types:
        conditions.append({"meeting": {"$in": list(meeting_types)}})
    if content_type:
        conditions.append({"content_type": {"$eq": content_type}})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _date_bounds(date_from, date_to):
    lo = f"{date_from}-01" if date_from else None
    hi = None
    if date_to:
        y, m = map(int, date_to.split("-"))
        _, last = monthrange(y, m)
        hi = f"{date_to}-{last:02d}"
    return lo, hi


# ── Main hybrid query function ────────────────────────────────

def query(
    question,
    n_results=N_RESULTS,
    collection_name=None,
    date_from=None,
    date_to=None,
    meeting_types=None,
    content_type=None,
):
    """
    Hybrid BM25 + semantic search with adaptive weighting.

    Query-type detection sets weights automatically:
      Resolution / dollar queries   → 30% semantic, 70% BM25
      Procedural language queries   → 40% semantic, 60% BM25
      Conceptual questions          → 70% semantic, 30% BM25

    Falls back to semantic-only if the BM25 index is not available.
    content_type filter degrades gracefully on legacy collections.
    """
    # Resolve collection
    col = _default_collection
    if collection_name:
        try:
            col = _client.get_collection(collection_name)
        except Exception:
            pass
    if col is None:
        return []

    sem_weight, bm25_weight = _detect_weights(question)
    lo, hi = _date_bounds(date_from, date_to)

    # ── Semantic search ──────────────────────────────────
    embedding = _model.encode(question).tolist()
    where     = _build_where(meeting_types, content_type)

    fetch_n = CANDIDATE_POOL * 3 if (date_from or date_to) else CANDIDATE_POOL
    fetch_n = min(fetch_n, col.count())

    q_kw = dict(
        query_embeddings=[embedding],
        n_results=fetch_n,
        include=["documents", "metadatas", "distances"],
    )
    if where:
        q_kw["where"] = where

    try:
        sem_raw = col.query(**q_kw)
    except Exception:
        # content_type may not exist in legacy collection — retry without it
        fallback_where = _build_where(meeting_types)
        q_kw_fb = dict(q_kw)
        if fallback_where:
            q_kw_fb["where"] = fallback_where
        else:
            q_kw_fb.pop("where", None)
        sem_raw = col.query(**q_kw_fb)

    sem_map: Dict = {}
    for doc, meta, dist in zip(
        sem_raw["documents"][0],
        sem_raw["metadatas"][0],
        sem_raw["distances"][0],
    ):
        d = meta.get("date", "")
        if lo and d < lo: continue
        if hi and d > hi: continue
        sem_map[doc] = {
            "text":              doc,
            "date":              d,
            "meeting":           meta.get("meeting", ""),
            "page":              meta.get("page", ""),
            "resolution_number": meta.get("resolution_number") or None,
            "content_type":      meta.get("content_type", "other"),
            "semantic_score":    max(0.0, round(1 - dist, 3)),
            "bm25_score":        0.0,
        }

    # ── BM25 search ──────────────────────────────────────
    bm25_map:      Dict = {}
    bm25_full_map: Dict = {}

    if _bm25 is not None:
        bm25_results = _bm25_mod.bm25_search(
            question, _bm25, _bm25_chunks, _bm25_meta,
            n_results=CANDIDATE_POOL,
            date_from=date_from, date_to=date_to,
            meeting_types=meeting_types, content_type=content_type,
        )
        bm25_map      = {r["text"]: r["bm25_score"] for r in bm25_results}
        bm25_full_map = {r["text"]: r               for r in bm25_results}

    # No BM25 available: pure semantic
    if not bm25_map:
        sem_weight, bm25_weight = 1.0, 0.0

    # ── Merge & rank ─────────────────────────────────────
    merged = []
    for text in set(sem_map) | set(bm25_map):
        s = sem_map[text]["semantic_score"] if text in sem_map else 0.0
        b = bm25_map.get(text, 0.0)

        if text in sem_map:
            entry = dict(sem_map[text])
        else:
            entry = dict(bm25_full_map[text])

        entry["semantic_score"] = s
        entry["bm25_score"]     = b
        entry["score"]          = round(sem_weight * s + bm25_weight * b, 3)
        merged.append(entry)

    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:n_results]


# ── CLI testing ──────────────────────────────────────────────

def print_results(question, results):
    print(f"\nQ: {question}")
    print("=" * 70)
    for i, r in enumerate(results):
        res = r.get('resolution_number') or '-'
        print(f"  {i+1}. score={r['score']:.3f} "
              f"(sem={r.get('semantic_score',0):.3f} "
              f"bm25={r.get('bm25_score',0):.3f}) "
              f"| {r['date']} p{r['page']} "
              f"| {r.get('content_type','?')} | res:{res}")
        print(f"     {r['text'][:120]}...")
    print()


if __name__ == "__main__":
    test = [
        "any major changes in denver parks?",
        "colorado coalition for the homeless contract amount",
        "did council discuss climate policy",
        "what color is the sun",
    ]
    for q in test:
        print_results(q, query(q))
        input("Enter for next...")
