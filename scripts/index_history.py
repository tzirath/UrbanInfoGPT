"""
Indexes Denver City Council meeting history quarter by quarter.

Indexing a full year at once ran out of RAM (zsh: killed).
Quarters (~250 rows each) fit comfortably in memory.

Safe to interrupt and resume — each quarter is checked against
the manifest before fetching, so nothing is re-indexed.

Usage:
    python -u scripts/index_history.py
"""
import sys
import os
import time
import json
from datetime import date as _date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from pipeline import run_pipeline, MANIFEST_PATH

# ── CONFIGURATION ──────────────────────────────────────────────
YEARS_TO_INDEX = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
MEETING_TYPES  = ["CityCouncil"]
PAUSE_SECONDS  = 2   # pause between quarters


# ── HELPERS ────────────────────────────────────────────────────

def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return {"segments": [], "total_chunks": 0}
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def quarters_for_year(year: int):
    """
    Returns list of (date_from, date_to) quarter strings for the year.
    Current year is trimmed to the current month.
    """
    today   = _date.today()
    all_q   = [
        (f"{year}-01", f"{year}-03"),
        (f"{year}-04", f"{year}-06"),
        (f"{year}-07", f"{year}-09"),
        (f"{year}-10", f"{year}-12"),
    ]
    if year != today.year:
        return all_q

    result = []
    for q_from, q_to in all_q:
        if int(q_from[5:7]) > today.month:
            break
        if int(q_to[5:7]) > today.month:
            q_to = f"{year}-{today.month:02d}"
        result.append((q_from, q_to))
    return result


def is_quarter_indexed(date_from, date_to, meeting_types, manifest):
    for mt in meeting_types:
        found = any(
            mt in seg.get("meeting_types", [])
            and seg.get("date_from") == date_from
            and seg.get("date_to")   == date_to
            for seg in manifest.get("segments", [])
        )
        if not found:
            return False
    return True


def index_quarter(date_from, date_to, meeting_types):
    """Run pipeline for one quarter. Returns (success, chunks_added)."""
    print(f"\n  ── {date_from} → {date_to} ──────────────────")
    start = time.time()
    try:
        result  = run_pipeline(meeting_types=meeting_types,
                               date_from=date_from, date_to=date_to)
        elapsed = time.time() - start
        chunks  = result.get("chunks_added", 0)
        print(f"  ✓ Done in {elapsed/60:.1f} min  |  {chunks:,} chunks")
        return True, chunks
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        return False, 0


# ── MAIN ───────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "═" * 52)
    print("  UrbanInfoGPT — History Indexer (by quarter)")
    print("═" * 52)

    # Build full work list
    all_quarters = []
    for year in YEARS_TO_INDEX:
        for q in quarters_for_year(year):
            all_quarters.append((year, q[0], q[1]))

    # Preview
    manifest  = load_manifest()
    to_do     = [(y, f, t) for y, f, t in all_quarters
                 if not is_quarter_indexed(f, t, MEETING_TYPES, manifest)]
    to_skip   = len(all_quarters) - len(to_do)

    print(f"\n  {len(all_quarters)} quarters total  |  "
          f"{len(to_do)} to index  |  {to_skip} already done\n")
    for _, f, t in to_do:
        print(f"  ▶  {f} → {t}")

    if not to_do:
        print("\nAll quarters indexed. Nothing to do.")
        print("Run: python scripts/run_analytics.py")
        sys.exit(0)

    est_min = len(to_do) * 5
    print(f"\n  Estimated: ~{est_min} min  ({est_min/60:.1f} h)")
    print("  Starting in 5 seconds…  (Ctrl+C to cancel)")
    time.sleep(5)

    # Index
    total_start   = time.time()
    total_chunks  = 0
    failed        = []
    current_year  = None

    for year, date_from, date_to in to_do:
        if year != current_year:
            current_year = year
            print(f"\n{'═' * 52}")
            print(f"  {year}")
            print(f"{'═' * 52}")

        manifest = load_manifest()
        if is_quarter_indexed(date_from, date_to, MEETING_TYPES, manifest):
            print(f"  ⏭  {date_from} → {date_to} (already indexed)")
            continue

        ok, chunks = index_quarter(date_from, date_to, MEETING_TYPES)
        if ok:
            total_chunks += chunks
        else:
            failed.append(f"{date_from}→{date_to}")

        time.sleep(PAUSE_SECONDS)

    elapsed_total = time.time() - total_start
    print(f"\n{'═' * 52}")
    print(f"  DONE  |  {elapsed_total/60:.0f} min  |  {total_chunks:,} chunks added")
    if failed:
        print(f"  ⚠  Failed quarters: {failed}")
        print(f"     Re-run to retry.")
    print(f"\n  Next steps:")
    print(f"    python scripts/run_analytics.py")
    print(f"    python scripts/clear_cache.py")
    print(f"    python dashboard.py")
    print(f"{'═' * 52}\n")
