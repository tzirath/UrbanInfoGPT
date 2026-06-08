"""
Indexes Denver City Council meeting history (2020-2024).
2025 CityCouncil data is already indexed in "denver_all".

Designed to run overnight — safe to interrupt and resume.
Checks the manifest before each year to skip already-indexed data.
Re-running is idempotent: deterministic chunk IDs mean upsert
never creates duplicates even if the manifest check is bypassed.

Usage:
    python scripts/index_history.py

To add committee data after CityCouncil completes, change MEETING_TYPES.
"""
import sys
import os
import time
import json
from datetime import date as _date

# Project root is one level above scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from pipeline import run_pipeline, MANIFEST_PATH

# ── CONFIGURATION ──────────────────────────────────────────────
# Oldest-first keeps the manifest growing chronologically.
YEARS_TO_INDEX = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

# Start with CityCouncil only — largest and most analytically valuable.
# Add committees by updating this list after CityCouncil finishes.
MEETING_TYPES = ["CityCouncil"]

# Seconds to pause between years (polite to the remote API).
PAUSE_BETWEEN_YEARS = 3


# ── MANIFEST HELPERS ───────────────────────────────────────────

def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        return {"segments": [], "total_chunks": 0}
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def is_already_indexed(year: int, meeting_types: list, manifest: dict) -> bool:
    """
    True if every meeting type for this year already appears in the manifest.
    Manifest stores dates as 'YYYY-MM' (e.g. '2024-01', '2024-12').
    For the current year we only check date_from so a month advance
    (e.g. 2026-05 → 2026-06) triggers a fresh index run.
    """
    today = _date.today()
    for mt in meeting_types:
        found = False
        for seg in manifest.get("segments", []):
            date_from_match = seg.get("date_from") == f"{year}-01"
            date_to_match   = (
                seg.get("date_to") == _date_to_for_year(year)
                if year == today.year
                else seg.get("date_to") == f"{year}-12"
            )
            if mt in seg.get("meeting_types", []) and date_from_match and date_to_match:
                found = True
                break
        if not found:
            return False
    return True


# ── INDEXING ───────────────────────────────────────────────────

def _date_to_for_year(year: int) -> str:
    """December for past years; current month for the current year."""
    today = _date.today()
    if year == today.year:
        return f"{year}-{today.month:02d}"
    return f"{year}-12"


def index_year(year: int, meeting_types: list):
    """
    Index one calendar year (or year-to-date for the current year).
    Returns (success: bool, chunks_added: int).
    """
    date_from = f"{year}-01"
    date_to   = _date_to_for_year(year)

    print(f"\n{'═' * 52}")
    print(f"  Indexing {year}  |  {', '.join(meeting_types)}")
    print(f"{'═' * 52}")

    start = time.time()
    try:
        result = run_pipeline(
            meeting_types=meeting_types,
            date_from=date_from,
            date_to=date_to,
        )
        elapsed = time.time() - start
        chunks  = result.get("chunks_added", 0)
        print(f"  ✓ {year} done in {elapsed / 60:.1f} min  |  {chunks:,} chunks added")
        return True, chunks

    except Exception as e:
        elapsed = time.time() - start
        print(f"  ✗ {year} FAILED after {elapsed / 60:.1f} min: {e}")
        print(f"    Continuing with next year...")
        return False, 0


# ── MAIN ───────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "═" * 52)
    print("  UrbanInfoGPT — History Indexer")
    print("  Denver City Council  2020 → 2024")
    print("═" * 52)

    manifest = load_manifest()

    # ── Preview ───────────────────────────────────────────────
    to_index = []
    to_skip  = []
    for year in YEARS_TO_INDEX:
        if is_already_indexed(year, MEETING_TYPES, manifest):
            to_skip.append(year)
        else:
            to_index.append(year)

    print("\nPlan:")
    for year in to_index:
        print(f"  ▶  {year}  ({', '.join(MEETING_TYPES)})")
    for year in to_skip:
        print(f"  ⏭  {year}  (already in manifest — skipping)")

    if not to_index:
        print("\nAll years already indexed. Nothing to do.")
        print("Next: python scripts/run_analytics.py")
        sys.exit(0)

    est_min = len(to_index) * 15   # rough: ~15 min per year on Mac CPU
    print(f"\nEstimated time: ~{est_min} min  ({est_min / 60:.1f} h)")
    print("Safe to leave running overnight.")
    print("\nStarting in 5 seconds…  (Ctrl+C to cancel)")
    time.sleep(5)

    # ── Run ───────────────────────────────────────────────────
    total_start = time.time()
    succeeded   = []
    failed      = []
    skipped     = []
    total_added = 0

    for i, year in enumerate(YEARS_TO_INDEX):
        # Re-read manifest each iteration in case a prior year just wrote it.
        manifest = load_manifest()

        if is_already_indexed(year, MEETING_TYPES, manifest):
            print(f"\n  ⏭  Skipping {year} (already indexed)")
            skipped.append(year)
            continue

        ok, chunks = index_year(year, MEETING_TYPES)

        if ok:
            succeeded.append(year)
            total_added += chunks
        else:
            failed.append(year)

        # Pause between years (not after the last one).
        remaining = [y for y in YEARS_TO_INDEX[i + 1:] if y not in skipped]
        if remaining:
            print(f"\n  Pausing {PAUSE_BETWEEN_YEARS}s before {remaining[0]}…")
            time.sleep(PAUSE_BETWEEN_YEARS)

    # ── Summary ───────────────────────────────────────────────
    elapsed_total = time.time() - total_start

    print(f"\n{'═' * 52}")
    print(f"  INDEXING COMPLETE")
    print(f"{'═' * 52}")
    print(f"  Total time : {elapsed_total / 60:.0f} min")
    print(f"  Chunks added: {total_added:,}")
    print(f"  Succeeded : {succeeded}")
    print(f"  Skipped   : {skipped}")

    if failed:
        print(f"  ⚠  Failed  : {failed}")
        print(f"     Re-run this script to retry failed years.")

    print(f"\n{'═' * 52}")
    print("  NEXT STEPS")
    print("  1. Rebuild analytics with full history:")
    print("       python scripts/run_analytics.py")
    print("  2. Clear answer cache:")
    print("       python scripts/clear_cache.py")
    print("  3. Restart dashboard:")
    print("       python dashboard.py")
    print(f"{'═' * 52}\n")
