#!/usr/bin/env python3
"""
Rebuild all analytics from raw data files.

Usage:
    python scripts/run_analytics.py
"""
import sys
import os

# Run from project root regardless of where the script is called from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.votes import build_vote_analytics
from analytics.financials import build_financial_analytics


def main():
    os.makedirs("data/analytics", exist_ok=True)

    print("\n── Vote Analytics ──────────────────────────────")
    votes = build_vote_analytics()

    print("\n── Financial Analytics ─────────────────────────")
    financials = build_financial_analytics()

    # Print summary to terminal
    print("\n══ Summary ════════════════════════════════════")

    v_summary = (votes or {}).get("summary", {})
    members   = (votes or {}).get("members", {})
    if members:
        ranked = sorted(members.items(), key=lambda x: x[1]["nay_count"], reverse=True)
        top    = ranked[0]
        print(f"Parsed {v_summary.get('total_votes_parsed', 0)} vote records")
        print(f"  {v_summary.get('contested_votes', 0)} contested  |  "
              f"{v_summary.get('unanimous_votes', 0)} unanimous")
        print(f"Top dissenter: {top[0]} ({top[1]['nay_count']} Nay votes, "
              f"{top[1]['nay_rate']:.1%} dissent rate)")

        most_absent = max(members.items(), key=lambda x: x[1]["absent_count"])
        print(f"Most absent:   {most_absent[0]} ({most_absent[1]['absent_count']} absences)")

    f_summary = (financials or {}).get("summary", {})
    if f_summary:
        print(f"\nParsed {f_summary.get('total_contracts', 0)} financial transactions "
              f"totaling {f_summary.get('total_value_formatted', '?')}")
        largest = f_summary.get("largest_contract")
        if largest:
            print(f"Largest contract: {largest['vendor']} "
                  f"{largest.get('amount_formatted', '?')} "
                  f"({largest['date']})")
        by_cat = f_summary.get("by_category", {})
        if by_cat:
            top_cat = list(by_cat.items())[0]
            print(f"Top category: {top_cat[0].replace('_',' ').title()} "
                  f"{top_cat[1]['formatted']} ({top_cat[1]['count']} contracts)")

    print("\nFiles saved to data/analytics/")


if __name__ == "__main__":
    main()
