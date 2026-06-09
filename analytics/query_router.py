"""
Detect question type and route to the right data source.
Returns pre-computed analytics as a formatted context string
to be passed to Claude alongside RAG chunks.
"""
import re

RESOLUTION_PATTERN = re.compile(r'\b(\d{2}-\d{4})\b')
MAX_AUTO_LOOKUPS = 3  # controls cost — ChromaDB only, no LLM calls


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


# ── Topic keyword map ────────────────────────────────────────
_TOPIC_KEYWORDS = {
    "housing":        ["housing", "affordable", "rezoning", "rezone", "residential",
                       "apartment", "condo", "rent", "tenant", "landlord", "hud",
                       "inclusionary", "accessory dwelling", "adu"],
    "homelessness":   ["homeless", "shelter", "encampment", "supportive housing",
                       "navigation center", "unhoused", "transitional"],
    "transportation": ["transportation", "transit", "bus", "rail", "bike", "road",
                       "highway", "rtd", "scooter", "pedestrian", "crosswalk",
                       "mobility", "traffic"],
    "climate":        ["climate", "environment", "green", "sustainability", "emissions",
                       "carbon", "renewable", "solar", "energy", "compost", "recycle"],
    "immigration":    ["immigrant", "immigration", "newcomer", "sanctuary", "refugee",
                       "undocumented", "asylum", "visa"],
    "public safety":  ["police", "safety", "crime", "fire", "emergency", "sheriff",
                       "911", "dispatcher", "gun", "weapon"],
    "education":      ["education", "school", "dps", "denver public schools",
                       "library", "teacher", "student", "curriculum"],
    "aviation":       ["airport", "dia", "aviation", "airline", "terminal", "concourse"],
    "budget":         ["budget", "fiscal", "appropriation", "fund", "allocation",
                       "expenditure", "revenue", "tax", "mill levy"],
    "parks":          ["park", "recreation", "trail", "open space", "playground",
                       "mountain parks", "golf"],
}

_YEAR_RE = re.compile(r'\b(20\d{2})\b')


def _extract_filters(question: str):
    """Return (year_str | None, [topic_keywords])."""
    q   = question.lower()
    yr  = (_YEAR_RE.search(question) or [None])[0]   # "2025" or None
    kws = []
    for topic, words in _TOPIC_KEYWORDS.items():
        if any(w in q for w in [topic] + words[:3]):   # check topic name + first 3 keywords
            kws.extend(words)
    return yr, list(set(kws))


def _filter_bills(nay_bills: list, year: str, keywords: list) -> list:
    """Filter a member's nay_bills list by year and/or topic keywords."""
    result = []
    for bill in nay_bills:
        if year and not bill.get("date", "").startswith(year):
            continue
        if keywords:
            desc = (bill.get("description") or "").lower()
            if not any(kw in desc for kw in keywords):
                continue
        result.append(bill)
    return result


def format_vote_context(question: str, votes_data: dict) -> str:
    members = votes_data.get("members", {})
    summary = votes_data.get("summary", {})
    if not members:
        return ""

    year, keywords = _extract_filters(question)
    topic_filtered = bool(keywords)
    year_filtered  = bool(year)

    # Build per-member filtered stats
    member_stats = []
    for name, s in members.items():
        filtered_bills = _filter_bills(s.get("nay_bills", []), year, keywords)
        nay_n = len(filtered_bills)
        # Absent dates are not linked to specific bills — only show when no topic filter
        absent_n = 0
        if not topic_filtered:
            absent_n = len([d for d in s.get("absent_dates", [])
                            if not year or d.startswith(year)])

        if topic_filtered or year_filtered:
            if nay_n == 0 and absent_n == 0:
                continue
        else:
            nay_n          = s["nay_count"]
            absent_n       = s["absent_count"]
            filtered_bills = s.get("nay_bills", [])
        member_stats.append((name, nay_n, absent_n, filtered_bills, s))

    # If topic keywords matched nothing (truncated descriptions), fall back to year-only
    # so Claude still gets the full list of 2025 nay votes to cross-reference with RAG chunks
    topic_fallback = False
    if topic_filtered and not member_stats:
        topic_fallback = True
        for name, s in members.items():
            filtered_bills = _filter_bills(s.get("nay_bills", []), year, [])
            nay_n = len(filtered_bills)
            if nay_n == 0:
                continue
            member_stats.append((name, nay_n, 0, filtered_bills, s))

    member_stats.sort(key=lambda x: x[1], reverse=True)

    # Header
    scope_parts = []
    if year_filtered:
        scope_parts.append(year)
    if topic_filtered:
        # Recover the matched topic name(s) for the header label
        q = question.lower()
        matched = [t for t, ws in _TOPIC_KEYWORDS.items()
                   if t in q or any(w in q for w in ws[:3])]
        scope_parts.append(" / ".join(matched) if matched else "topic")
    scope = " · ".join(scope_parts) if scope_parts else "all years · all legislation"
    lines = [f"VOTE ANALYTICS ({scope}):\n"]

    if not member_stats:
        lines.append("No Nay votes found for this combination.")
        return "\n".join(lines)

    if topic_fallback:
        lines.append(
            "Note: bill descriptions are too short to match topic keywords directly. "
            "Showing ALL nay votes for the requested year — cross-reference with the "
            "meeting-minutes excerpts above to identify which bills are housing-related.\n"
        )

    lines.append("Council members with Nay votes (filtered to your query):")
    for name, nay_n, absent_n, bills, raw in member_stats:
        total = raw["total_votes"] if not (topic_filtered or year_filtered) else "n/a"
        rate  = f"{raw['nay_rate']:.1%}" if not (topic_filtered or year_filtered) else ""
        lines.append(
            f"  {name}: {nay_n} Nay vote{'s' if nay_n != 1 else ''}"
            + (f" ({rate} overall dissent rate)" if rate else "")
            + (f", {absent_n} absence{'s' if absent_n != 1 else ''}" if absent_n else "")
        )

    # Bill details for top dissenters (up to top 3)
    for name, nay_n, absent_n, bills, _ in member_stats[:3]:
        if not bills:
            continue
        lines.append(f"\n{name}'s relevant Nay votes:")
        for bill in bills[:8]:
            desc   = (bill.get("description") or "")[:90] or "(no description)"
            others = ", ".join(bill.get("other_nays", [])) or "lone dissent"
            lines.append(
                f"  - {bill['resolution']} ({bill['date']}): {desc} "
                f"[{bill['bill_result']}] — others Nay: {others}"
            )

    # Most contested votes — filter if applicable
    contested = summary.get("most_contested_bills", [])
    if year_filtered:
        contested = [b for b in contested if b.get("date", "").startswith(year)]
    if topic_filtered:
        contested = [b for b in contested
                     if any(kw in (b.get("description") or "").lower() for kw in keywords)]
    if contested:
        lines.append("\nMost contested votes (matching your query):")
        for b in contested[:5]:
            lines.append(
                f"  - {b['resolution']} ({b['date']}): "
                f"{b['nay_count']} Nays — {', '.join(b['nays'])}"
            )

    if not (topic_filtered or year_filtered):
        lines.append(
            f"\nSummary: {summary.get('contested_votes', 0)} contested, "
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


# ── Resolution context enrichment ────────────────────────────

def enrich_with_resolution_context(analytics_context: str,
                                    filters: dict = None) -> str:
    """
    Scans analytics_context for resolution numbers (e.g. 25-0713).
    Runs a targeted RAG search for each one found.
    Returns the enriched context string.

    Cost: up to MAX_AUTO_LOOKUPS ChromaDB searches — no LLM calls.
    """
    if not analytics_context:
        return analytics_context

    resolutions = list(set(RESOLUTION_PATTERN.findall(analytics_context)))
    if not resolutions:
        return analytics_context

    # Lazy import avoids loading heavy ML models when analytics runs standalone
    from query import query as rag_query

    enrichments = []
    for resolution in resolutions[:MAX_AUTO_LOOKUPS]:
        try:
            results = rag_query(
                f"resolution {resolution}",
                n_results=2,
                **(filters or {}),
            )
            good_results = [r for r in results if r["score"] > 0.15]
            if good_results:
                best = good_results[0]
                description = best["text"][:300].strip()
                enrichments.append(
                    f"\nContext for Resolution {resolution} "
                    f"({best['date']}, Page {best['page']}):\n"
                    f"{description}..."
                )
        except Exception as e:
            print(f"Auto-lookup failed for {resolution}: {e}")
            continue

    if not enrichments:
        return analytics_context

    return analytics_context + "\n\nRESOLUTION DETAILS:\n" + "\n".join(enrichments)


# ── Public entry point ───────────────────────────────────────

def get_analytics_context(question: str, analytics_data: dict,
                           filters: dict = None) -> tuple:
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
        if ctx:
            ctx = enrich_with_resolution_context(ctx, filters=filters)
        return (ctx or None), "votes"

    if qtype == "financial":
        ctx = format_financial_context(question, analytics_data.get("financials") or {})
        if ctx:
            ctx = enrich_with_resolution_context(ctx, filters=filters)
        return (ctx or None), "financial"

    return None, "rag"
