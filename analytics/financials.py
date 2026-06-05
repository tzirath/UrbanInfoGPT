"""
Extract financial records (contracts, agreements, appropriations)
from meeting minutes text and build aggregated analytics.
"""
import glob
import json
import os
import re
from collections import defaultdict
from datetime import datetime

_BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR       = os.path.join(_BASE, "data", "raw")
ANALYTICS_DIR = os.path.join(_BASE, "data", "analytics")

# "Approves a contract with Vendor Inc for $1,234,567.00"
# "Amends a contract with Vendor LLC to add $450,000"
FINANCIAL_RE = re.compile(
    r'(Approve[sd]?|Amend[sd]?)\s+(?:a\s+)?(?:proposed\s+)?'
    r'(?:contract|agreement)\s+with\s+'
    r'(.+?)\s+'
    r'(?:to add|for)\s+'
    r'(\$[\d,]+(?:\.\d{2})?)',
    re.IGNORECASE,
)

# Standalone dollar amounts (for summary counts)
DOLLAR_RE    = re.compile(r'\$[\d,]+(?:\.\d{2})?')
RES_DESC_RE  = re.compile(r'(\d{2,4}-\d{3,4})\s+A\s+(?:resolution|bill|ordinance)', re.IGNORECASE)
DISTRICT_RE  = re.compile(r'Council District\s+(\d+)', re.IGNORECASE)
END_DATE_RE  = re.compile(r'end date of\s+(\d{1,2}-\d{2}-\d{4})', re.IGNORECASE)

CATEGORIES = {
    "homeless_services": [
        "homeless", "shelter", "rehousing", "unhoused",
        "coalition for the homeless", "recovery housing", "transitional housing",
    ],
    "infrastructure": [
        "road", "pavement", "bridge", "construction", "sewer",
        "water main", "street", "drainage", "utility",
    ],
    "parks": ["parks", "recreation", "landscape", "open space", "trail", "forestry"],
    "aviation": ["airport", "dia", "aviation", "airline", "terminal", "concourse"],
    "housing": ["affordable housing", "housing authority", "residential", "homeownership"],
    "technology": ["software", "hardware", "technology", "it services", "digital", "data"],
    "health": ["health", "medical", "hospital", "behavioral health", "mental health"],
    "legal": ["legal", "settlement", "claim", "lawsuit"],
}


def _parse_amount(raw: str) -> float:
    try:
        return float(re.sub(r'[$,]', '', raw))
    except Exception:
        return 0.0


def _format_amount(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _categorize(text: str) -> str:
    t = text.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in t for kw in keywords):
            return cat
    return "other"


def _clean_vendor(raw: str) -> str:
    """Remove trailing preposition artifacts from vendor name capture."""
    v = re.sub(r'\s+(?:for|to)\s*$', '', raw, flags=re.IGNORECASE).strip()
    return re.sub(r'[,;]\s*$', '', v).strip()


def build_financial_analytics() -> dict:
    os.makedirs(ANALYTICS_DIR, exist_ok=True)

    all_rows = []
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*.json"))):
        with open(path) as f:
            all_rows.extend(json.load(f))

    if not all_rows:
        print("  No raw data found in", RAW_DIR)
        return {}

    raw_contracts = []

    for row in all_rows:
        text = row.get('text', '')
        if not text:
            continue

        # Nearest resolution number mentioned anywhere on this page
        page_res_m = RES_DESC_RE.search(text)
        page_res   = page_res_m.group(1) if page_res_m else None

        for m in FINANCIAL_RE.finditer(text):
            action_word = m.group(1).lower()
            vendor      = _clean_vendor(m.group(2))
            amount_raw  = m.group(3)
            amount      = _parse_amount(amount_raw)

            if amount <= 0 or not vendor:
                continue

            action = "amendment" if re.search(r'amend', action_word) else "new"

            # Context window around this mention
            ctx_start = max(0, m.start() - 400)
            ctx       = text[ctx_start: m.end() + 200]

            # More specific resolution number near this mention
            local_res_m = RES_DESC_RE.search(ctx)
            resolution  = local_res_m.group(1) if local_res_m else page_res

            district_m = DISTRICT_RE.search(ctx)
            district   = f"District {district_m.group(1)}" if district_m else "citywide"

            end_date_m = END_DATE_RE.search(ctx)
            end_date   = end_date_m.group(1) if end_date_m else ""

            # Description: resolution line or surrounding context
            desc_m = re.search(
                r'(?:resolution|bill|ordinance)[^\n]{0,200}',
                ctx, re.IGNORECASE,
            )
            description = desc_m.group(0)[:150].strip() if desc_m else ctx[:100].strip()

            raw_contracts.append({
                'resolution':        resolution,
                'date':              row['date'],
                'meeting':           row.get('meeting', ''),
                'vendor':            vendor,
                'amount':            amount,
                'amount_raw':        amount_raw,
                'amount_formatted':  _format_amount(amount),
                'action':            action,
                'end_date':          end_date,
                'council_district':  district,
                'description':       description,
                'category':          _categorize(description + ' ' + vendor),
            })

    print(f"  Found {len(raw_contracts)} raw financial mentions")

    # Deduplicate: same vendor + amount + date
    seen: set = set()
    contracts = []
    for c in raw_contracts:
        key = (c['date'], c['vendor'][:40].lower(), c['amount'])
        if key not in seen:
            seen.add(key)
            contracts.append(c)

    print(f"  {len(contracts)} unique contracts after deduplication")

    if not contracts:
        return {}

    # ── Aggregations ─────────────────────────────────────────
    total_value = sum(c['amount'] for c in contracts)

    # By category
    by_cat: dict = defaultdict(lambda: {'total': 0.0, 'count': 0})
    for c in contracts:
        by_cat[c['category']]['total'] += c['amount']
        by_cat[c['category']]['count'] += 1
    by_category = {
        k: {
            'total':     round(v['total'], 2),
            'count':     v['count'],
            'formatted': _format_amount(v['total']),
        }
        for k, v in sorted(by_cat.items(), key=lambda x: -x[1]['total'])
    }

    # By vendor (top 10)
    by_vendor: dict = defaultdict(lambda: {'total': 0.0, 'count': 0, 'category': 'other'})
    for c in contracts:
        by_vendor[c['vendor']]['total']    += c['amount']
        by_vendor[c['vendor']]['count']    += 1
        by_vendor[c['vendor']]['category']  = c['category']
    by_vendor_top10 = [
        {
            'vendor':    k,
            'total':     round(v['total'], 2),
            'count':     v['count'],
            'formatted': _format_amount(v['total']),
            'category':  v['category'],
        }
        for k, v in sorted(by_vendor.items(), key=lambda x: -x[1]['total'])[:10]
    ]

    # By district
    by_district: dict = defaultdict(float)
    for c in contracts:
        by_district[c['council_district']] += c['amount']

    # Monthly spending
    by_month: dict = defaultdict(float)
    for c in contracts:
        by_month[c['date'][:7]] += c['amount']
    monthly_spending = [
        {'month': k, 'total': round(v, 2), 'formatted': _format_amount(v)}
        for k, v in sorted(by_month.items())
    ]

    largest = max(contracts, key=lambda x: x['amount'])

    output = {
        'generated_at': datetime.utcnow().isoformat(),
        'summary': {
            'total_contracts':       len(contracts),
            'total_value':           round(total_value, 2),
            'total_value_formatted': _format_amount(total_value),
            'by_category':           by_category,
            'by_vendor_top10':       by_vendor_top10,
            'by_district':           {k: round(v, 2) for k, v in by_district.items()},
            'largest_contract':      largest,
            'monthly_spending':      monthly_spending,
        },
        'contracts': sorted(contracts, key=lambda x: -x['amount']),
    }

    out_path = os.path.join(ANALYTICS_DIR, "financials.json")
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"  Total value: {_format_amount(total_value)}")
    print(f"  Largest: {largest['vendor']} {largest['amount_formatted']}")
    print(f"  Saved → {out_path}")

    return output
