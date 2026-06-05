"""
Detect question type and route to the right data source.
Returns pre-computed analytics as a formatted context string
to be passed to Claude alongside RAG chunks.
"""
import re


# ── Question type detection ──────────────────────────────────

_VOTE_PATTERNS = [
    r'who voted',
    r'who rejected',
    r'who opposed',
    r'voting record',
    r'voted against',
    r'nay vote',
    r'dissenting',
    r'most dissent',
    r'most opposition',
    r'against the most',
    r'who refused',
    r'council member.{0,40}most',
    r'most absent',
    r'how did.{0,30}vote',
    r'\babsent\b',
    r'opposition to',
    r'rejected most',
]

_FINANCIAL_PATTERNS = [
    r'how much',
    r'total.{0,20}cost',
    r'total.{0,20}spend',
    r'\$[\d,]+',
    r'contract.{0,20}amount',
    r'most expensive',
    r'largest contract',
    r'most contracts',
    r'how many contracts',
    r'\bdollars\b',
    r'\bspending\b',
    r'\bbudget\b',
    r'\bfunding\b',
    r'which company',
    r'which vendor',
    r'which firm',
    r'how much.*paid',
    r'how much.*cost',
    r'how much.*spent',
]


def detect_question_type(question: str) -> str:
    """Returns 'votes' | 'financial' | 'rag'"""
    q = question.lower()
    for pattern in _VOTE_PATTERNS:
        if re.search(pattern, q):
            return "votes"
    for pattern in _FINANCIAL_PATTERNS:
        if re.search(pattern, q):
            return "financial"
    return "rag"


# ── Context formatters ───────────────────────────────────────

def _fmt(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def format_vote_context(question: str, votes_data: dict) -> str:
    members = votes_data.get("members", {})
    summary = votes_data.get("summary", {})

    if not members:
        return ""

    ranked = sorted(
        members.items(),
        key=lambda x: x[1]["nay_count"],
        reverse=True,
    )

    lines = ["VOTE ANALYTICS (pre-computed from all City Council records):\n"]
    lines.append("Council members ranked by dissenting (Nay) votes:")
    for name, s in ranked:
        if s['total_votes'] == 0 and s['absent_count'] == 0:
            continue
        lines.append(
            f"  {name}: {s['nay_count']} Nay votes "
            f"({s['nay_rate']:.1%} dissent rate), "
            f"{s['absent_count']} absences, "
            f"{s['total_votes']} total votes cast"
        )

    # Detailed Nay history for the top 2 dissenters
    for name, s in ranked[:2]:
        if not s['nay_bills']:
            continue
        lines.append(f"\n{name}'s Nay votes:")
        for bill in s['nay_bills'][:6]:
            desc = bill['description'][:80] if bill['description'] else '(no description)'
            others = ', '.join(bill['other_nays']) if bill['other_nays'] else 'lone dissent'
            lines.append(
                f"  - {bill['resolution']} ({bill['date']}): {desc} "
                f"[{bill['bill_result']}] — others Nay: {others}"
            )

    # Most contested votes
    contested_bills = summary.get("most_contested_bills", [])[:5]
    if contested_bills:
        lines.append("\nMost contested votes (by Nay count):")
        for b in contested_bills:
            nay_names = ', '.join(b['nays'])
            lines.append(
                f"  - {b['resolution']} ({b['date']}): "
                f"{b['nay_count']} Nays — {nay_names}"
            )

    lines.append(
        f"\nSummary: {summary.get('contested_votes', 0)} contested votes, "
        f"{summary.get('unanimous_votes', 0)} unanimous, "
        f"{summary.get('total_votes_parsed', 0)} total parsed."
    )

    return "\n".join(lines)


def format_financial_context(question: str, financials_data: dict) -> str:
    summary = financials_data.get("summary", {})

    if not summary:
        return ""

    lines = ["FINANCIAL ANALYTICS (pre-computed from all City Council records):\n"]
    lines.append(
        f"Total tracked spending: {summary.get('total_value_formatted', '?')} "
        f"across {summary.get('total_contracts', 0)} contracts"
    )

    by_cat = summary.get("by_category", {})
    if by_cat:
        lines.append("\nBy category:")
        for cat, data in list(by_cat.items())[:8]:
            label = cat.replace('_', ' ').title()
            lines.append(f"  {label}: {data['formatted']} ({data['count']} contracts)")

    top_vendors = summary.get("by_vendor_top10", [])
    if top_vendors:
        lines.append("\nTop vendors by contract value:")
        for i, v in enumerate(top_vendors[:8], 1):
            lines.append(f"  {i}. {v['vendor']}: {v['formatted']} ({v['count']} contracts)")

    largest = summary.get("largest_contract")
    if largest:
        lines.append(
            f"\nLargest single contract: {largest['vendor']} "
            f"{largest.get('amount_formatted', _fmt(largest.get('amount', 0)))} "
            f"({largest['date']}) — {largest['description'][:80]}"
        )

    return "\n".join(lines)


# ── Public entry point ───────────────────────────────────────

def get_analytics_context(question: str, analytics_data: dict) -> tuple:
    """
    Returns (context_string | None, question_type).
    context_string is passed to Claude alongside RAG chunks.
    question_type is 'votes' | 'financial' | 'rag'.
    """
    if not analytics_data:
        return None, "rag"

    qtype = detect_question_type(question)

    if qtype == "votes":
        ctx = format_vote_context(question, analytics_data.get("votes") or {})
        return (ctx or None), "votes"

    if qtype == "financial":
        ctx = format_financial_context(question, analytics_data.get("financials") or {})
        return (ctx or None), "financial"

    return None, "rag"
