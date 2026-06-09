# src/ingest.py
import warnings
warnings.filterwarnings("ignore")

import requests
import json
import os
import time
from datetime import date as _date

# ── CONFIGURATION ──────────────────────────────────────────
BASE_URL      = "https://denver.co.civic.band/meetings/minutes.json"
MEETING_TYPES = ["CityCouncil"]
YEARS         = list(range(2020, _date.today().year + 1))
PAGE_SIZE     = 100
OUTPUT_DIR    = "data/raw"


# ── FETCH ───────────────────────────────────────────────────
def fetch_year(meeting_type, year):
    today     = _date.today()
    date_from = f"{year}-01-01"
    date_to   = f"{year}-12-31" if year < today.year else today.strftime("%Y-%m-%d")

    print(f"  Fetching {meeting_type} {year} ({date_from} → {date_to})...")

    all_rows, cursor = [], None
    while True:
        params = {
            "meeting":    meeting_type,
            "date__gte":  date_from,
            "date__lte":  date_to,
            "_size":      PAGE_SIZE,
            "_sort_desc": "date",
        }
        if cursor:
            params["_next"] = cursor

        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"    Error: status {resp.status_code}")
            break

        data = resp.json()
        rows = data.get("rows", [])
        if not rows:
            break

        all_rows.extend(rows)
        print(f"    {len(all_rows)} rows so far...")

        cursor = data.get("next")
        if not cursor:
            break
        time.sleep(0.3)

    print(f"    ✓ {len(all_rows)} rows")
    return all_rows


# ── SAVE ────────────────────────────────────────────────────
def save_year(meeting_type, year, rows):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{meeting_type}_{year}.json")
    with open(path, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"    Saved → {path}")


# ── MAIN ────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\nIngesting {YEARS[0]}–{YEARS[-1]}\n")
    for meeting_type in MEETING_TYPES:
        for year in YEARS:
            rows = fetch_year(meeting_type, year)
            if rows:
                save_year(meeting_type, year, rows)
    print("\nIngestion complete!")
