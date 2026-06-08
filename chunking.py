import re
import bisect
from typing import List, Tuple, Dict

# Matches the start of a new agenda item, e.g. "\n25-0545 A resolution..."
RESOLUTION_PATTERN = re.compile(
    r'\n(\d{2}-\d{4})\s+A\s+(resolution|bill|ordinance|proclamation|presentation)',
    re.IGNORECASE,
)
VOTE_PATTERN = re.compile(r'^(Aye|Nay)\s*:', re.MULTILINE)
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')

MAX_RES_CHARS   = 800
PROC_CHUNK_SIZE = 300
PROC_OVERLAP    = 50
RES_OVERLAP     = 100
MIN_CHUNK_CHARS = 50


# ── Text splitters ────────────────────────────────────────────

def _split_at_sentences(text: str, max_size: int, overlap: int) -> List[str]:
    """Split long text at sentence boundaries, keeping chunks ≤ max_size."""
    ends = [m.end() for m in SENTENCE_END.finditer(text)]
    chunks, start = [], 0
    while start < len(text):
        end = start + max_size
        if end >= len(text):
            tail = text[start:].strip()
            if tail:
                chunks.append(tail)
            break
        # Last sentence boundary within the window — O(log n) via bisect
        lo   = bisect.bisect_right(ends, start)
        hi   = bisect.bisect_right(ends, end)
        best = ends[hi - 1] if hi > lo else None
        if best:
            c = text[start:best].strip()
            if c:
                chunks.append(c)
            start = max(0, best - overlap)
        else:
            # No sentence boundary — cut at last word boundary
            wb = text.rfind(' ', start, end)
            cut = wb if wb > start else end
            c = text[start:cut].strip()
            if c:
                chunks.append(c)
            start = max(0, cut - overlap)
    return chunks


def _split_procedural(text: str) -> List[str]:
    """Split non-resolution text into ~300-char word-boundary chunks."""
    chunks, i = [], 0
    while i < len(text):
        end = i + PROC_CHUNK_SIZE
        if end < len(text):
            wb = text.rfind(' ', i, end)
            if wb > i:
                end = wb
        c = text[i:end].strip()
        if c:
            chunks.append(c)
        i = end - PROC_OVERLAP
    return chunks


# ── Main chunking functions ───────────────────────────────────

def chunk_page(text: str, base_meta: Dict) -> List[Tuple[str, Dict]]:
    """
    Chunk one page of meeting minutes by resolution boundary.

    Resolution boundaries keep the full motion + vote together.
    Long resolutions are split at sentence boundaries with the
    resolution number prepended to continuation chunks.

    Returns list of (chunk_text, metadata) tuples.
    metadata keys: meeting, date, page, content_type,
                   resolution_number, chunk_index
    """
    text    = text.replace('\r\n', '\n').replace('\r', '\n')
    results: List[Tuple[str, Dict]] = []
    matches = list(RESOLUTION_PATTERN.finditer(text))

    def _meta(content_type: str, res_num: str = "") -> Dict:
        return {
            **base_meta,
            "content_type":      content_type,
            "resolution_number": res_num,
            "chunk_index":       len(results),
        }

    # ── No resolutions on this page ───────────────────────
    if not matches:
        for c in _split_procedural(text):
            c = c.strip()
            if len(c) >= MIN_CHUNK_CHARS:
                ctype = "vote" if VOTE_PATTERN.search(c) else "procedural"
                results.append((c, _meta(ctype)))
        return results

    # ── Procedural content before the first resolution ────
    pre = text[:matches[0].start()].strip()
    if len(pre) >= MIN_CHUNK_CHARS:
        for c in _split_procedural(pre):
            c = c.strip()
            if len(c) >= MIN_CHUNK_CHARS:
                results.append((c, _meta("procedural")))

    # ── Each resolution block ────────────────────────────
    for idx, m in enumerate(matches):
        res_num     = m.group(1)
        block_start = m.start() + 1   # +1 skips the leading \n
        block_end   = (matches[idx + 1].start()
                       if idx + 1 < len(matches) else len(text))
        res_text    = text[block_start:block_end].strip()

        if not res_text:
            continue

        ctype = "vote" if VOTE_PATTERN.search(res_text) else "resolution"

        if len(res_text) <= MAX_RES_CHARS:
            results.append((res_text, _meta(ctype, res_num)))
        else:
            sub_chunks = _split_at_sentences(
                res_text, MAX_RES_CHARS, RES_OVERLAP
            )
            for j, sc in enumerate(sub_chunks):
                sc = sc.strip()
                if len(sc) < MIN_CHUNK_CHARS:
                    continue
                # Prepend resolution number to continuation chunks so
                # context is preserved even when retrieved in isolation.
                stored = sc if j == 0 else f"[Resolution {res_num}] {sc}"
                results.append((stored, _meta(ctype, res_num)))

    return results


def chunk_documents(rows: List[Dict]) -> List[Tuple[str, Dict]]:
    """
    Chunk all rows from the raw API JSON.
    Returns list of (chunk_text, metadata) tuples ready for embedding.
    """
    all_chunks: List[Tuple[str, Dict]] = []
    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        base_meta = {
            "meeting": row.get("meeting", ""),
            "date":    row.get("date", ""),
            "page":    row.get("page", 0),
        }
        all_chunks.extend(chunk_page(text, base_meta))
    return all_chunks
