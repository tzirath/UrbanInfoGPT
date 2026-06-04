# src/ingest.py
import warnings
warnings.filterwarnings("ignore")

import requests
import json
import os
import time

# ── CONFIGURATION ──────────────────────────────────────────
BASE_URL = "https://denver.co.civic.band/meetings/minutes.json"

MEETING_TYPES = ["CityCouncil"]

DATE_FROM = "2025-01-01"
DATE_TO   = "2025-12-31"

PAGE_SIZE = 100
OUTPUT_DIR = "data/raw"


# ── MAIN FUNCTION ───────────────────────────────────────────
def fetch_meeting_minutes(meeting_type):
    """
    Fetches all minutes using cursor-based pagination.
    The API returns a 'next' cursor to get the next page.
    """

    all_rows = []
    page = 1
    next_cursor = None  # starts as None, gets filled after first request

    print(f"Fetching {meeting_type} minutes ({DATE_FROM} to {DATE_TO})...")

    while True:
        # Build params - add cursor if we have one
        params = {
            "meeting": meeting_type,
            "date__gte": DATE_FROM,
            "date__lte": DATE_TO,
            "_size": PAGE_SIZE,
            "_sort_desc": "date",  # newest first
        }

        # After first page, use cursor to get next page
        if next_cursor:
            params["_next"] = next_cursor

        response = requests.get(BASE_URL, params=params)

        if response.status_code != 200:
            print(f"Error: status {response.status_code}")
            break

        data = response.json()
        rows = data.get("rows", [])

        if not rows:
            print(f"  Done! {len(all_rows)} total rows.")
            break

        all_rows.extend(rows)
        print(f"  Page {page}: {len(rows)} rows "
              f"(total: {len(all_rows)})")

        # Get next cursor - if None, we're on the last page
        next_cursor = data.get("next")
        if not next_cursor:
            print(f"  Done! {len(all_rows)} total rows.")
            break

        page += 1
        time.sleep(0.3)

    return all_rows


def save_data(meeting_type, rows):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"{OUTPUT_DIR}/{meeting_type}_2025.json"
    with open(filename, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"Saved to {filename}")


if __name__ == "__main__":
    for meeting_type in MEETING_TYPES:
        rows = fetch_meeting_minutes(meeting_type)
        save_data(meeting_type, rows)
    print("\nIngestion complete!")