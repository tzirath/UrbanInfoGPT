"""
Parse vote records from meeting minutes page text.

votes_json in the raw data is empty for all rows — vote data lives
in the text field as structured plain-text blocks.

Vote block structure:
  ...Council Resolution/Bill XX-XXXX be [action]...
  ...carried by the following vote:

  [Result line]

  Aye: Name, Name, Name,
  Name, Name (count)
  Nay: (None) (0)   OR   Name, Name (count)
  Absent: Name (count)     [optional]
  Abstain: Name (count)    [optional, rare]
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

FOLLOWING_VOTE_RE = re.compile(r'following vote:?\s*\n', re.IGNORECASE)
RES_REF_RE        = re.compile(
    r'Council (?:Resolution|Bill)s?\s+([\d]{2,4}-[\d]{3,4})',
    re.IGNORECASE,
)
RES_DESC_RE = re.compile(
    r'([\d]{2,4}-[\d]{3,4})\s+A\s+(?:resolution|bill|ordinance)',
    re.IGNORECASE,
)

# Second-pass: Aye: blocks NOT preceded by "following vote:"
_BARE_AYE_RE = re.compile(r'\nAye:\s*', re.IGNORECASE)

_RESULT_KEYWORD_MAP = [
    (r'final consideration and do pass', 'Passed'),
    (r'failed',                          'Failed'),
    (r'amended',                         'Amended'),
    (r'postponed',                       'Postponed'),
    (r'adopted',                         'Adopted'),
    (r'approved',                        'Approved'),
    (r'en bloc',                         'En Bloc'),
]


# ── Name parsing ─────────────────────────────────────────────

def _parse_names(raw: str) -> list:
    """'Name1, Name2,\nName3 (N)' → ['Name1', 'Name2', 'Name3']"""
    if not raw or '(None)' in raw:
        return []
    # Collapse newlines then remove trailing count like (12)
    text = re.sub(r'\s+', ' ', raw.strip())
    text = re.sub(r'\s*\(\d+\)\s*$', '', text)
    names = []
    for part in text.split(','):
        name = part.strip()
        # Valid: starts uppercase, at least 3 chars, no trailing colon
        if name and len(name) >= 3 and re.match(r'^[A-Z]', name) and not name.endswith(':'):
            names.append(name)
    return names


# ── Description extraction ───────────────────────────────────

def _extract_description(res_num: str, text: str) -> str:
    """Look for 'XX-XXXX A resolution...' near the top of a page."""
    if not res_num:
        return ""
    m = re.search(
        re.escape(res_num) + r'\s+A\s+(?:resolution|bill|ordinance)\s+([^\n]{0,200})',
        text, re.IGNORECASE,
    )
    if m:
        return (res_num + " A " + m.group(0).split(' A ', 1)[-1]).strip()[:180]
    return ""


# ── Vote-block parser ────────────────────────────────────────

def _parse_vote_blocks(text: str, row_date: str, row_meeting: str) -> list:
    """Extract every vote record from one page's text."""
    records = []
    seen_on_page = set()

    for fv in FOLLOWING_VOTE_RE.finditer(text):
        vote_pos = fv.end()

        # Scan backwards for the nearest Council Resolution/Bill reference
        preceding = text[max(0, fv.start() - 600): fv.start()]
        res_matches = list(RES_REF_RE.finditer(preceding))
        res_num = res_matches[-1].group(1) if res_matches else None

        # Grab a generous window of text after "following vote:"
        block = text[vote_pos: vote_pos + 700]
        lines = block.split('\n')

        # Result line: first non-empty line
        idx = 0
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        result = lines[idx].strip() if idx < len(lines) else ""

        # Skip junk result lines (page numbers, etc.)
        if result.startswith('Page ') or not result:
            continue

        section = '\n'.join(lines[idx + 1:])

        aye_m    = re.search(r'Aye:\s*(.*?)(?=\nNay:|\nAbsent:|\nAbstain:|\n\n|\Z)', section, re.DOTALL)
        nay_m    = re.search(r'Nay:\s*(.*?)(?=\nAbsent:|\nAbstain:|\n\n|\Z)',         section, re.DOTALL)
        absent_m = re.search(r'Absent:\s*(.*?)(?=\nAbstain:|\n\n|\Z)',                 section, re.DOTALL)
        abstain_m= re.search(r'Abstain:\s*(.*?)(?=\n\n|\Z)',                           section, re.DOTALL)

        if not aye_m:
            continue

        ayes    = _parse_names(aye_m.group(1))
        nays    = _parse_names(nay_m.group(1)    if nay_m    else '')
        absent  = _parse_names(absent_m.group(1) if absent_m else '')
        abstain = _parse_names(abstain_m.group(1)if abstain_m else '')

        if not ayes:
            continue

        # Dedup within the page: same voter composition + result
        dedup = (frozenset(ayes), frozenset(nays), result)
        if dedup in seen_on_page:
            continue
        seen_on_page.add(dedup)

        records.append({
            'resolution': res_num,
            'result':     result,
            'ayes':       ayes,
            'nays':       nays,
            'absent':     absent,
            'abstain':    abstain,
            'date':       row_date,
            'meeting':    row_meeting,
        })

    return records


# ── Second-pass parser: formats without "following vote:" ────

def _parse_direct_vote_blocks(text: str, row_date: str, row_meeting: str) -> list:
    """
    Catches vote blocks NOT anchored by 'following vote:'.
    Handles: 'Placed upon final consideration', 'Failed',
    'Amended', 'Postponed', 'Adopted', 'Approved', en-bloc.
    """
    records = []
    seen    = set()

    for m in _BARE_AYE_RE.finditer(text):
        # Skip if already captured by the "following vote:" parser
        pre = text[max(0, m.start() - 700): m.start()]
        if 'following vote:' in pre.lower():
            continue

        # Determine result from the 200 chars immediately preceding Aye:
        result = 'Unknown'
        pre200 = pre[-200:]
        for keyword, label in _RESULT_KEYWORD_MAP:
            if re.search(keyword, pre200, re.IGNORECASE):
                result = label
                break

        # Parse the vote block (everything after "Aye: ")
        block   = text[m.end(): m.end() + 500]
        nay_m   = re.search(r'\nNay:\s*(.*?)(?=\nAbsent:|\nAbstain:|\n\n|\Z)', block, re.DOTALL)
        if not nay_m:
            continue  # malformed — no Nay: line

        aye_text   = block[:nay_m.start()]
        nay_text   = nay_m.group(1)
        absent_m   = re.search(r'\nAbsent:\s*(.*?)(?=\nAbstain:|\n\n|\Z)', block, re.DOTALL)
        absent_text = absent_m.group(1) if absent_m else ''

        ayes   = _parse_names(aye_text)
        nays   = _parse_names(nay_text)
        absent = _parse_names(absent_text)

        if not ayes:
            continue

        dedup = (frozenset(ayes), frozenset(nays), result)
        if dedup in seen:
            continue
        seen.add(dedup)

        # Nearest resolution number in the 600 chars before Aye:
        pre600      = text[max(0, m.start() - 600): m.start()]
        res_matches = list(RES_REF_RE.finditer(pre600))
        if not res_matches:
            res_matches = list(RES_DESC_RE.finditer(pre600))
        res_num = res_matches[-1].group(1) if res_matches else None

        records.append({
            'resolution': res_num,
            'result':     result,
            'ayes':       ayes,
            'nays':       nays,
            'absent':     absent,
            'abstain':    [],
            'date':       row_date,
            'meeting':    row_meeting,
        })

    return records


# ── Main build function ──────────────────────────────────────

def build_vote_analytics() -> dict:
    os.makedirs(ANALYTICS_DIR, exist_ok=True)

    all_rows = []
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*.json"))):
        with open(path) as f:
            all_rows.extend(json.load(f))

    if not all_rows:
        print("  No raw data found in", RAW_DIR)
        return {}

    # ── Parse every page ─────────────────────────────────────
    all_votes = []
    seen_global = set()   # (date, ayes, nays, result) → dedup across pages

    for row in all_rows:
        text = row.get('text', '')
        if not text:
            continue
        has_fv  = 'following vote:' in text.lower()
        has_aye = '\naye:'           in text.lower()
        if not (has_fv or has_aye):
            continue

        raw_records: list = []
        if has_fv:
            raw_records.extend(_parse_vote_blocks(text, row['date'], row.get('meeting', '')))
        if has_aye:
            raw_records.extend(_parse_direct_vote_blocks(text, row['date'], row.get('meeting', '')))

        for record in raw_records:
            # Global dedup: same voter composition + result + date = same vote
            key = (record['date'], frozenset(record['ayes']),
                   frozenset(record['nays']), record['result'])
            if key in seen_global:
                continue
            seen_global.add(key)

            record['description'] = _extract_description(record['resolution'], text)
            all_votes.append(record)

    print(f"  Parsed {len(all_votes)} unique vote records")

    # ── Per-member aggregation ────────────────────────────────
    members: dict = defaultdict(lambda: {
        'total_votes':   0,
        'aye_count':     0,
        'nay_count':     0,
        'absent_count':  0,
        'abstain_count': 0,
        'nay_rate':      0.0,
        'nay_bills':     [],
        'absent_dates':  [],
        'lone_nay_count': 0,
    })

    for v in all_votes:
        for name in v['ayes']:
            members[name]['total_votes'] += 1
            members[name]['aye_count']   += 1

        for name in v['nays']:
            members[name]['total_votes'] += 1
            members[name]['nay_count']   += 1
            members[name]['nay_bills'].append({
                'resolution':  v['resolution'] or 'unknown',
                'date':        v['date'],
                'meeting':     v['meeting'],
                'description': v['description'][:100] if v['description'] else '',
                'bill_result': v['result'],
                'other_nays':  [n for n in v['nays'] if n != name],
            })

        for name in v['absent']:
            members[name]['absent_count'] += 1
            members[name]['absent_dates'].append(v['date'])

        for name in v['abstain']:
            members[name]['abstain_count'] += 1

    for stats in members.values():
        t = stats['total_votes']
        stats['nay_rate']       = round(stats['nay_count'] / t, 4) if t > 0 else 0.0
        stats['lone_nay_count'] = sum(1 for b in stats['nay_bills'] if not b['other_nays'])
        stats['nay_bills'].sort(key=lambda x: x['date'])
        stats['absent_dates'].sort()

    # ── Summary ───────────────────────────────────────────────
    total_parsed = len(all_votes)
    unanimous    = sum(1 for v in all_votes if not v['nays'])
    contested    = sum(1 for v in all_votes if v['nays'])

    most_contested = sorted(
        [v for v in all_votes if v['nays']],
        key=lambda x: len(x['nays']),
        reverse=True,
    )[:10]

    dates = sorted(set(v['date'] for v in all_votes))

    output = {
        'generated_at': datetime.utcnow().isoformat(),
        'summary': {
            'total_votes_parsed':    total_parsed,
            'unanimous_votes':       unanimous,
            'contested_votes':       contested,
            'most_contested_bills': [{
                'resolution':  v['resolution'] or 'unknown',
                'date':        v['date'],
                'description': v['description'][:100] if v['description'] else '',
                'nay_count':   len(v['nays']),
                'nays':        v['nays'],
                'result':      v['result'],
            } for v in most_contested],
            'date_range': {
                'from': dates[0]  if dates else '',
                'to':   dates[-1] if dates else '',
            },
        },
        'members': dict(members),
    }

    out_path = os.path.join(ANALYTICS_DIR, "votes.json")
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)

    # Summary print
    ranked = sorted(members.items(), key=lambda x: x[1]['nay_count'], reverse=True)
    print(f"  Top dissenter: {ranked[0][0]} ({ranked[0][1]['nay_count']} Nay votes)")
    print(f"  Contested: {contested}  Unanimous: {unanimous}")
    print(f"  Saved → {out_path}")

    return output
