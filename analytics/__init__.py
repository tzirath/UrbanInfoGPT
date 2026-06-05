import json
import os

_BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYTICS_DIR = os.path.join(_BASE, "data", "analytics")


def run_all_analytics() -> dict:
    """Rebuild all analytics from data/raw/ and save to data/analytics/."""
    from .votes import build_vote_analytics
    from .financials import build_financial_analytics

    os.makedirs(ANALYTICS_DIR, exist_ok=True)

    print("Building vote analytics...")
    votes = build_vote_analytics()

    print("Building financial analytics...")
    financials = build_financial_analytics()

    print("Analytics complete.")
    return {"votes": votes, "financials": financials}


def load_analytics():
    """
    Load pre-computed analytics from disk.
    Returns None if either file is missing — run scripts/run_analytics.py first.
    """
    votes_path = os.path.join(ANALYTICS_DIR, "votes.json")
    fin_path   = os.path.join(ANALYTICS_DIR, "financials.json")

    if not os.path.exists(votes_path) or not os.path.exists(fin_path):
        return None

    with open(votes_path) as f:
        votes = json.load(f)
    with open(fin_path) as f:
        financials = json.load(f)

    return {"votes": votes, "financials": financials}
