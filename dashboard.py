import warnings
warnings.filterwarnings("ignore")

import sys
import os
import json
from collections import defaultdict
from datetime import date as _date_cls

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import dash
from dash import Dash, html, dcc, Input, Output, State, ALL, ctx, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

import pipeline
from query import query
from llm import get_answer
from utils.links import build_datasette_url, build_image_url

try:
    from agents.query_refiner import refined_search, get_last_refined_queries
except Exception as _ref_err:
    print(f"Query refinement unavailable: {_ref_err}")
    def refined_search(question, n_results=5, **kw):
        return query(question, n_results=n_results, **kw)
    def get_last_refined_queries():
        return []

try:
    from analytics import load_analytics
    from analytics.query_router import get_analytics_context
    _analytics = load_analytics()
    if _analytics:
        print("Analytics loaded.")
    else:
        print("Analytics not found — run scripts/run_analytics.py to enable.")
except Exception as _e:
    _analytics = None
    print(f"Analytics unavailable: {_e}")

# ── DESIGN TOKENS ─────────────────────────────────────────────
AMBER       = "#F59E0B"
AMBER_LIGHT = "#FEF3C7"
AMBER_DARK  = "#D97706"
NAVY        = "#1a1f2e"
SUCCESS     = "#10B981"
DANGER      = "#EF4444"
BORDER      = "#F3F4F6"
TEXT        = "#1F2937"
MUTED       = "#6B7280"

COMMITTEE_COLOR = {
    "CityCouncil":                                                         "#3B82F6",
    "SpecialMeetingOfTheCityCouncil":                                      "#3B82F6",
    "LandUse,TransportationAndInfrastructureCommittee":                    "#8B5CF6",
    "FinanceAndGovernanceCommittee":                                       "#10B981",
    "Safety,Housing,EducationAndHomelessnessCommittee":                    "#EF4444",
    "Business,Arts,Workforce,ClimateAndAviationServicesCommittee":         "#F59E0B",
    "Parks,ArtAndCulture":                                                 "#6EE7B7",
    "TransportationandInfrastructure":                                     "#8B5CF6",
    "SafetyAndWell-beingCommittee":                                        "#EF4444",
    "BudgetandPolicyCommittee":                                            "#10B981",
}

# ── MEETING GROUPS ────────────────────────────────────────────
MEETING_GROUPS = {
    "Full Council": [
        ("CityCouncil",                                                       12875),
        ("SpecialMeetingOfTheCityCouncil",                                       53),
    ],
    "Committees": [
        ("LandUse,TransportationAndInfrastructureCommittee",                  1731),
        ("FinanceAndGovernanceCommittee",                                     1084),
        ("Safety,Housing,EducationAndHomelessnessCommittee",                   939),
        ("Business,Arts,Workforce,ClimateAndAviationServicesCommittee",        574),
        ("TransportationandInfrastructure",                                    141),
        ("SafetyAndWell-beingCommittee",                                       124),
        ("BudgetandPolicyCommittee",                                            94),
        ("FinanceandBusiness",                                                  71),
        ("CommunityPlanningandHousing",                                         67),
        ("GovernanceandIntergovernmentalRelations",                              64),
        ("HealthandSafety",                                                     62),
        ("Parks,ArtAndCulture",                                                 27),
        ("RedistrictingCommittee",                                              10),
        ("BudgetHearings",                                                       8),
    ],
    "Working Groups": [
        ("EmergencyResponseWorkingGroup",                                        8),
        ("GeneralObligationBondWorkingGroup",                                    4),
        ("HousingandHomelessnessWorkingGroup",                                   4),
        ("PublicSafetyWorkingGroup",                                             4),
        ("NewcomerResponseWorkingGroup",                                         2),
    ],
    "Special Issues": [
        ("SouthPlatteRiverCommittee",                                           89),
        ("SpecialIssuesRedistricting",                                          34),
        ("SpecialIssuesMarijuana",                                              32),
        ("SpecialIssuesCityCharter",                                            10),
        ("SpecialIssues:GreenRoofInitiative",                                    5),
        ("Mayor-Council",                                                        4),
        ("PolicyCommittee",                                                      2),
        ("GeneralPublicCommentSession",                                          1),
        ("OperationsCityCouncil",                                                1),
    ],
}

ALL_MEETING_TYPES = [mt for grp in MEETING_GROUPS.values() for mt, _ in grp]
_TYPE_ROWS        = {mt: cnt for grp in MEETING_GROUPS.values() for mt, cnt in grp}

TYPE_FILTER_OPTIONS = sorted(
    [{"label": f"{mt}  ({cnt:,})", "value": mt}
     for grp in MEETING_GROUPS.values() for mt, cnt in grp],
    key=lambda o: -_TYPE_ROWS[o["value"]],
)

EXAMPLE_QUESTIONS = [
    "Who opposed housing legislation?",
    "DIA contracts 2025",
    "Climate ordinance votes",
    "Budget decisions",
    "Immigrant services incentives",
    "Homeless shelter funding",
]

# ── STARTER TOPICS ────────────────────────────────────────────
STARTER_TOPICS = [
    "Affordable Housing",
    "Homelessness",
    "Climate & Environment",
    "Transportation",
    "Immigration",
    "Public Safety",
    "Education",
]

# ── MONTH OPTIONS ─────────────────────────────────────────────
def _month_options():
    from datetime import date
    today = date.today()
    opts, d, end = [], date(2020, 1, 1), date(today.year, today.month, 1)
    while d <= end:
        opts.append({"label": d.strftime("%b %Y"), "value": d.strftime("%Y-%m")})
        m, y = d.month + 1, d.year
        if m > 12:
            m, y = 1, y + 1
        d = date(y, m, 1)
    return opts

MONTH_OPTIONS = _month_options()

def _group_checklist(group_id, group_name, types):
    return html.Div([
        html.Div(group_name,
                 style={"fontSize": ".68rem", "fontWeight": "700", "color": NAVY,
                        "textTransform": "uppercase", "letterSpacing": ".07em",
                        "marginBottom": "5px", "paddingBottom": "3px",
                        "borderBottom": f"2px solid {NAVY}"}),
        dbc.Checklist(
            id=group_id,
            options=[
                {"label": html.Span([
                    html.Span(mt, style={"fontSize": ".78rem"}),
                    html.Span(f" ({cnt:,})", style={"fontSize": ".7rem", "color": MUTED}),
                ]), "value": mt}
                for mt, cnt in types
            ],
            value=["CityCouncil"] if group_id == "types-fullcouncil" else [],
            input_style={"cursor": "pointer"},
            label_style={"marginBottom": "3px", "cursor": "pointer"},
        ),
    ])

# ── APP INIT ──────────────────────────────────────────────────
_init_coll     = pipeline.get_active_collection_name()
_init_coverage = pipeline.get_coverage()
_initial_store = (
    {"collection_name": _init_coll, "chunks": _init_coverage["total_chunks"]}
    if _init_coll else None
)

app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        dbc.icons.BOOTSTRAP,
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    ],
    suppress_callback_exceptions=True,
    title="🏛️ UrbanInfoGPT",
)

# ── CHART BUILDERS ────────────────────────────────────────────
_CHART_BASE = dict(
    paper_bgcolor="white", plot_bgcolor="white",
    font=dict(family="Inter, system-ui, sans-serif", size=11, color=TEXT),
    margin=dict(l=0, r=16, t=8, b=0),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                font=dict(size=10)),
)


def _vote_chart(votes_data, year=None):
    """Build voting patterns chart. year=None or 'all' shows all three bars lifetime.
    year='2024' (specific) shows nay and absent for that year only."""
    members = votes_data.get("members", {})
    if not members:
        return go.Figure()

    if year and year != "all":
        # Per-year: rank by nay count in that year
        def _nay_for_year(stats):
            return sum(1 for b in stats.get("nay_bills", []) if (b.get("date") or "")[:4] == year)
        def _absent_for_year(stats):
            return sum(1 for d in stats.get("absent_dates", []) if (d or "")[:4] == year)

        ranked = sorted(
            [(name, stats) for name, stats in members.items()],
            key=lambda x: _nay_for_year(x[1]),
            reverse=True,
        )[:12]
        names   = [r[0] for r in ranked][::-1]
        nays    = [_nay_for_year(members[n]) for n in names]
        absents = [_absent_for_year(members[n]) for n in names]

        fig = go.Figure()
        fig.add_trace(go.Bar(name="Nay",    x=nays,    y=names, orientation="h",
                             marker_color=DANGER,    hovertemplate="%{y}: %{x} Nay<extra></extra>"))
        fig.add_trace(go.Bar(name="Absent", x=absents, y=names, orientation="h",
                             marker_color="#9CA3AF", hovertemplate="%{y}: %{x} Absent<extra></extra>"))
        title_text = f"Voting Patterns — {year}"
    else:
        # All years — full lifetime stats
        ranked  = sorted(members.items(), key=lambda x: x[1]["nay_count"], reverse=True)[:12]
        names   = [r[0] for r in ranked][::-1]
        ayes    = [members[n]["aye_count"]    for n in names]
        nays    = [members[n]["nay_count"]    for n in names]
        absents = [members[n]["absent_count"] for n in names]

        fig = go.Figure()
        fig.add_trace(go.Bar(name="Aye",    x=ayes,    y=names, orientation="h",
                             marker_color=SUCCESS,   hovertemplate="%{y}: %{x} Aye<extra></extra>"))
        fig.add_trace(go.Bar(name="Nay",    x=nays,    y=names, orientation="h",
                             marker_color=DANGER,    hovertemplate="%{y}: %{x} Nay<extra></extra>"))
        fig.add_trace(go.Bar(name="Absent", x=absents, y=names, orientation="h",
                             marker_color="#9CA3AF", hovertemplate="%{y}: %{x} Absent<extra></extra>"))
        title_text = "Voting Patterns — All Years (2020–2026)"

    _base = {k: v for k, v in _CHART_BASE.items() if k != "margin"}
    fig.update_layout(
        **_base,
        showlegend=True,
        barmode="stack",
        height=340,
        xaxis=dict(showgrid=True, gridcolor="#F3F4F6", zeroline=False),
        yaxis=dict(showgrid=False),
        title=dict(text=title_text, font=dict(size=12, color=TEXT)),
        margin=dict(l=0, r=16, t=40, b=0),
    )
    return fig


def _vote_table_data(votes_data, year=None):
    """Build vote table rows. When year is specific, show nay/absent for that year; aye shows '—'."""
    members = votes_data.get("members", {})
    if not members:
        return []

    if year and year != "all":
        def _nay_for_year(stats):
            return sum(1 for b in stats.get("nay_bills", []) if (b.get("date") or "")[:4] == year)
        def _absent_for_year(stats):
            return sum(1 for d in stats.get("absent_dates", []) if (d or "")[:4] == year)

        rows = []
        for name, s in sorted(members.items(), key=lambda x: _nay_for_year(x[1]), reverse=True):
            nay_y    = _nay_for_year(s)
            absent_y = _absent_for_year(s)
            rows.append({
                "Member":  name,
                "Total":   "—",
                "Aye":     "—",
                "Nay":     nay_y,
                "Absent":  absent_y,
                "Dissent": "—",
            })
        return rows
    else:
        return [
            {
                "Member": name,
                "Total":  s["total_votes"],
                "Aye":    s["aye_count"],
                "Nay":    s["nay_count"],
                "Absent": s["absent_count"],
                "Dissent": f"{s['nay_rate']:.1%}",
            }
            for name, s in sorted(members.items(), key=lambda x: x[1]["nay_count"], reverse=True)
            if s["total_votes"] > 0
        ]


def _spending_bar_chart(fin_data, selected_cats=None):
    by_cat = fin_data.get("summary", {}).get("by_category", {})
    if not by_cat:
        return go.Figure()
    items = [(k.replace("_", " ").title(), v["total"], v["count"])
             for k, v in by_cat.items()
             if not selected_cats or k in selected_cats]
    items.sort(key=lambda x: x[1])
    labels  = [i[0] for i in items]
    values  = [i[1] / 1_000_000 for i in items]
    counts  = [i[2] for i in items]
    colors  = [AMBER if i % 2 == 0 else AMBER_DARK for i in range(len(labels))]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=colors,
        customdata=counts,
        hovertemplate="<b>%{y}</b><br>$%{x:.1f}M · %{customdata} contracts<extra></extra>",
    ))
    fig.update_layout(**_CHART_BASE, height=max(220, len(labels) * 36),
                      xaxis=dict(title="$M", showgrid=True, gridcolor="#F3F4F6", zeroline=False),
                      yaxis=dict(showgrid=False), showlegend=False)
    return fig


def _monthly_chart(fin_data):
    monthly = fin_data.get("summary", {}).get("monthly_spending", [])
    if not monthly:
        return go.Figure()
    months = [m["month"] for m in monthly]
    totals = [m["total"] / 1_000_000 for m in monthly]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=months, y=totals,
        fill="tozeroy",
        line=dict(color=AMBER, width=2),
        fillcolor="rgba(254,243,199,0.55)",
        hovertemplate="%{x}: $%{y:.1f}M<extra></extra>",
    ))
    fig.update_layout(**_CHART_BASE, height=220, showlegend=False,
                      xaxis=dict(showgrid=False, showline=False),
                      yaxis=dict(title="$M", showgrid=True, gridcolor="#F3F4F6", zeroline=False))
    return fig


def _district_chart(fin_data):
    by_d = fin_data.get("summary", {}).get("by_district", {})
    if not by_d:
        return go.Figure()
    items = sorted(by_d.items(), key=lambda x: x[1])
    labels = [i[0] for i in items]
    values = [i[1] / 1_000_000 for i in items]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=AMBER,
        hovertemplate="%{y}: $%{x:.1f}M<extra></extra>",
    ))
    fig.update_layout(**_CHART_BASE, height=max(180, len(labels) * 26),
                      xaxis=dict(title="$M", showgrid=True, gridcolor="#F3F4F6", zeroline=False),
                      yaxis=dict(showgrid=False), showlegend=False)
    return fig


# ── UI HELPERS ────────────────────────────────────────────────

def _score_bar(score):
    pct   = int(score * 100)
    cls   = "score-high" if pct >= 60 else ("score-mid" if pct >= 30 else "score-low")
    color = SUCCESS if pct >= 60 else (AMBER if pct >= 30 else DANGER)
    return html.Div([
        html.Div([
            html.Div(style={
                "width": f"{pct}%", "height": "4px", "borderRadius": "3px",
                "background": color,
            }),
        ], style={"height": "4px", "borderRadius": "3px", "background": "#F3F4F6",
                  "flex": "1", "overflow": "hidden"}),
        html.Span(f"{pct}%", style={"fontSize": ".68rem", "color": MUTED,
                                     "fontWeight": "600", "whiteSpace": "nowrap"}),
    ], style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "6px"})


def _source_card(i, chunk):
    meeting   = chunk.get("meeting") or ""
    badge_col = COMMITTEE_COLOR.get(meeting, "#9CA3AF")
    short_m   = meeting[:24] + "…" if len(meeting) > 24 else meeting
    res_num   = chunk.get("resolution_number")
    ctype     = chunk.get("content_type", "other")
    is_fetched = chunk.get("source") == "page_fetch"

    doc_url = build_datasette_url(
        str(chunk.get("meeting") or ""),
        str(chunk.get("date")    or ""),
        chunk.get("page") or "",
    )
    img_url = build_image_url(
        str(chunk.get("meeting") or ""),
        str(chunk.get("date")    or ""),
        chunk.get("page") or "",
    )

    return html.Div([
        html.Div([
            html.Span(short_m, className="src-type-badge",
                      style={"background": badge_col}),
            *([html.Span(f"Res {res_num}", className="src-type-badge ms-1",
                         style={"background": "#6B7280"})] if res_num else []),
            *([html.Span("context", className="src-type-badge ms-1",
                         style={"background": "#D1D5DB", "color": "#374151"})]
              if is_fetched else []),
        ], style={"marginBottom": "5px"}),

        html.Div([
            html.Span(chunk.get("date", ""), style={
                "fontSize": ".76rem", "fontWeight": "600",
                "color": NAVY, "marginRight": "8px",
            }),
            html.Span(f"p.{chunk.get('page', '')}", style={
                "fontSize": ".72rem", "color": MUTED,
            }),
        ], style={"marginBottom": "5px"}),

        _score_bar(chunk.get("score", 0)),

        html.P(chunk.get("text", "")[:180] + "…",
               style={"fontSize": ".78rem", "color": "#4B5563",
                      "marginBottom": "8px", "lineHeight": "1.5"}),

        html.Div([
            html.A([html.I(className="bi bi-file-text me-1"), "View"],
                   href=doc_url, target="_blank",
                   className="btn btn-sm btn-outline-primary src-link-btn me-1"),
            html.A([html.I(className="bi bi-image me-1"), "Image"],
                   href=img_url, target="_blank",
                   className="btn btn-sm btn-outline-secondary src-link-btn"),
        ]),
    ], className="src-card")


def _render_source_grid(chunks):
    if not chunks:
        return ""
    return html.Div([
        html.Div([
            html.I(className="bi bi-journal-text me-2",
                   style={"color": NAVY, "fontSize": ".82rem"}),
            html.Span("Sources", style={
                "fontWeight": "700", "color": NAVY, "fontSize": ".78rem",
                "textTransform": "uppercase", "letterSpacing": ".06em",
            }),
        ], className="d-flex align-items-center mb-2"),
        *[_source_card(i, c) for i, c in enumerate(chunks)],
    ])


def _search_strategy_panel(original, queries):
    other = [q for q in queries if q.lower() != original.lower()]
    if not other:
        return ""
    return html.Details([
        html.Summary([html.I(className="bi bi-search me-1"), "Search strategy"]),
        html.Div([
            html.Div([html.Span("Original: ", style={"fontWeight": "600"}), f'"{original}"'],
                     style={"fontSize": ".75rem", "color": "#4B5563", "marginBottom": "5px"}),
            html.Div("Also searched:", style={"fontSize": ".75rem", "fontWeight": "600",
                                               "color": NAVY, "marginBottom": "4px"}),
            *[html.Div([html.Span("• ", style={"color": AMBER}), q],
                       style={"fontSize": ".75rem", "color": "#4B5563", "paddingLeft": "8px"})
              for q in other],
        ], style={"padding": "8px 12px", "marginTop": "6px", "background": "#FFFBF5",
                  "borderRadius": "6px", "border": f"1px solid {BORDER}"}),
    ])


# ── CONTESTED CARD (improved) ─────────────────────────────────

def _contested_card(b, rank=None):
    """Improved contested vote card with rank, meeting minutes link, and voter names."""
    nay_count  = b.get("nay_count", 0)
    resolution = b.get("resolution") or "Unknown"
    date_str   = b.get("date", "")
    description = (b.get("description") or "")[:200]
    nay_voters  = b.get("nay_voters", [])

    # Format date nicely if possible
    try:
        d = _date_cls.fromisoformat(date_str)
        nice_date = d.strftime("%B %-d, %Y")
    except Exception:
        nice_date = date_str

    rank_label = f"#{rank} Most Contested — {nay_count} Nay Vote{'s' if nay_count != 1 else ''}" if rank else f"{nay_count} Nay Vote{'s' if nay_count != 1 else ''}"

    minutes_url = f"https://denver.co.civic.band/meetings/minutes?meeting=CityCouncil&date={date_str}"

    return html.Div([
        html.Div([
            html.Span(rank_label,
                      style={"fontWeight": "700", "fontSize": ".78rem",
                             "color": DANGER, "textTransform": "uppercase",
                             "letterSpacing": ".04em"}),
        ], style={"marginBottom": "6px"}),

        html.Div([
            html.Span(resolution,
                      style={"fontWeight": "700", "fontSize": ".92rem", "color": NAVY,
                             "marginRight": "10px"}),
            html.Span(nice_date,
                      style={"fontSize": ".75rem", "color": MUTED}),
        ], className="d-flex align-items-center flex-wrap mb-1"),

        html.Div(description,
                 style={"fontSize": ".8rem", "color": "#4B5563",
                        "lineHeight": "1.5", "marginBottom": "8px"}) if description else "",

        html.Div([
            html.Span("Voted Against By: ",
                      style={"fontWeight": "600", "fontSize": ".75rem",
                             "color": MUTED, "whiteSpace": "nowrap"}),
            html.Span(" · ".join(nay_voters) if nay_voters else "—",
                      style={"fontSize": ".75rem", "color": TEXT}),
        ], className="d-flex flex-wrap align-items-baseline mb-2") if nay_voters else "",

        html.A([
            "View Meeting Minutes ",
            html.I(className="bi bi-arrow-right"),
        ], href=minutes_url, target="_blank",
           style={"fontSize": ".75rem", "color": AMBER_DARK, "fontWeight": "600",
                  "textDecoration": "none"}),
    ], className="contested-card-new")


# ── CHAT LAYOUT ───────────────────────────────────────────────

def chat_layout():
    return html.Div([

        # Hero
        html.Div([
            html.Div([
                html.I(className="bi bi-building me-1"),
                "Denver City Council · Claude + RAG",
            ], className="hero-badge"),
            html.H1("Ask anything about Denver's City Council",
                    className="hero-title"),
            html.P("Powered by official meeting minutes, vote records, and financial data",
                   className="hero-sub"),
        ], className="chat-hero"),

        # ── Centred content ──────────────────────────────────
        html.Div([

            # Search card
            html.Div([
                dbc.Textarea(
                    id="question-input",
                    placeholder="e.g. Which council member opposed the most proposals in 2025?",
                    rows=3,
                    style={"border": "none", "outline": "none", "boxShadow": "none",
                           "resize": "none", "fontSize": "1rem", "padding": "0",
                           "background": "transparent"},
                ),
                html.Div(style={"height": "1px", "background": BORDER, "margin": "12px 0"}),
                # CHANGE 7: Remove plus-circle icon — just the submit button on the right
                html.Div([
                    dbc.Button(
                        [html.I(className="bi bi-send-fill me-2"), "Ask"],
                        id="ask-button",
                        className="submit-btn",
                    ),
                ], className="d-flex justify-content-end align-items-center"),
            ], className="search-card mb-3"),

            # Example pills
            html.Div([
                html.Span("Try: ", style={"fontSize": ".78rem", "color": MUTED,
                                          "fontWeight": "500", "marginRight": "6px",
                                          "whiteSpace": "nowrap"}),
                *[html.Button(
                    q,
                    id={"type": "example", "index": i},
                    n_clicks=0,
                    className="example-pill me-2 mb-1",
                ) for i, q in enumerate(EXAMPLE_QUESTIONS)],
            ], className="d-flex flex-wrap align-items-center mb-3"),

            # CHANGE 5: History pills section
            html.Div(id="history-pills-section"),

            # Filters
            html.Div([
                html.Button(
                    [html.I(className="bi bi-sliders me-1"), "Filters"],
                    id="filter-toggle-btn",
                    n_clicks=0,
                    className="filter-toggle-btn mb-2",
                ),
                dbc.Collapse(id="filter-collapse-area", is_open=False, children=[
                    html.Div([
                        html.Div([
                            html.Label("From", style={"fontSize": ".72rem", "fontWeight": "600",
                                                       "color": MUTED, "marginBottom": "3px"}),
                            dcc.Dropdown(id="filter-start", options=MONTH_OPTIONS,
                                         placeholder="Any", clearable=True,
                                         style={"fontSize": ".82rem", "minWidth": "120px"}),
                        ]),
                        html.Span("→", style={"color": MUTED, "padding": "0 6px",
                                               "alignSelf": "flex-end", "paddingBottom": "6px"}),
                        html.Div([
                            html.Label("To", style={"fontSize": ".72rem", "fontWeight": "600",
                                                     "color": MUTED, "marginBottom": "3px"}),
                            dcc.Dropdown(id="filter-end", options=MONTH_OPTIONS,
                                         placeholder="Any", clearable=True,
                                         style={"fontSize": ".82rem", "minWidth": "120px"}),
                        ]),
                        html.Div([
                            html.Label("Meeting types", style={"fontSize": ".72rem", "fontWeight": "600",
                                                                 "color": MUTED, "marginBottom": "3px"}),
                            dcc.Dropdown(
                                id="filter-types",
                                options=TYPE_FILTER_OPTIONS,
                                placeholder="All types",
                                multi=True,
                                clearable=True,
                                style={"fontSize": ".82rem", "minWidth": "200px", "flex": "1"},
                            ),
                        ], style={"flex": "1"}),
                        html.Div([
                            html.Label("Content", style={"fontSize": ".72rem", "fontWeight": "600",
                                                          "color": MUTED, "marginBottom": "3px"}),
                            dcc.Dropdown(
                                id="filter-content",
                                options=[
                                    {"label": "All content",     "value": ""},
                                    {"label": "Resolutions only", "value": "resolution"},
                                    {"label": "Votes only",       "value": "vote"},
                                ],
                                value="",
                                clearable=False,
                                style={"fontSize": ".82rem", "minWidth": "140px"},
                            ),
                        ]),
                        dbc.Button(
                            [html.I(className="bi bi-x me-1"), "Clear"],
                            id="btn-clear-filters", color="light", size="sm",
                            style={"fontSize": ".75rem", "border": f"1px solid {BORDER}",
                                   "alignSelf": "flex-end"},
                        ),
                    ], style={"display": "flex", "flexWrap": "wrap", "gap": "12px",
                               "alignItems": "flex-end", "background": "#F9FAFB",
                               "borderRadius": "10px", "padding": "12px 14px",
                               "border": f"1px solid {BORDER}"}),
                ]),
            ], className="mb-4"),

            # Results: answer left, sources right
            html.Div([
                dcc.Loading(id="loading", type="dot", color=AMBER, children=[
                    dbc.Row([
                        dbc.Col([
                            html.Div(id="answer-panel"),
                        ], width=12, lg=8),
                        dbc.Col([
                            html.Div(id="sources-panel", className="mb-2"),
                            dbc.Button(
                                [html.I(className="bi bi-chevron-down me-1"),
                                 "Show more sources"],
                                id="show-more-btn",
                                color="link",
                                size="sm",
                                n_clicks=0,
                                style={"display": "none", "fontSize": ".78rem",
                                       "color": MUTED, "paddingLeft": 0},
                                className="mb-2",
                            ),
                            html.Div(id="search-strategy-panel"),
                        ], width=12, lg=4),
                    ], className="g-4"),
                ]),
            ]),

        ], style={"maxWidth": "1080px", "margin": "0 auto"}),

    ], className="chat-page")


# ── ANALYTICS LAYOUT ──────────────────────────────────────────

def analytics_layout():
    v = (_analytics or {}).get("votes")       or {}
    f = (_analytics or {}).get("financials")  or {}
    vs = v.get("summary", {})
    fs = f.get("summary", {})

    # KPI values
    total_votes   = vs.get("total_votes_parsed", "—")
    contested     = vs.get("contested_votes",    "—")
    total_spend   = fs.get("total_value_formatted", "—")
    total_ctracts = fs.get("total_contracts", "—")

    members = v.get("members", {})
    top_d = max(members.items(), key=lambda x: x[1]["nay_count"]) if members else ("—", {})
    top_name  = top_d[0]
    top_rate  = f"{top_d[1].get('nay_rate', 0):.1%}" if members else "—"
    top_nays  = top_d[1].get("nay_count", 0)           if members else 0

    # Vote member table data (lifetime)
    vote_table_data = _vote_table_data(v)

    # Most contested bills
    contested_bills = vs.get("most_contested_bills", [])[:10]

    # Available years — CHANGE 3: single-select with "All Years" option
    _year_set = set()
    for c in f.get("contracts", []):
        if c.get("date"):
            _year_set.add(c["date"][:4])
    for stats in members.values():
        for b in stats.get("nay_bills", []):
            if b.get("date"):
                _year_set.add(b["date"][:4])
    years_available = sorted(_year_set, reverse=True)

    year_filter_opts = (
        [{"label": "All Years", "value": "all"}]
        + [{"label": str(y), "value": str(y)} for y in range(2026, 2019, -1)]
    )

    # Category filter pills
    cats_available = list(f.get("summary", {}).get("by_category", {}).keys())
    cat_labels = {
        "homeless_services": "Homeless Services",
        "infrastructure":    "Infrastructure",
        "aviation":          "Aviation",
        "housing":           "Housing",
        "technology":        "Technology",
        "health":            "Health",
        "legal":             "Legal",
        "parks":             "Parks",
        "other":             "Other",
    }

    # Top contracts table
    contracts = f.get("contracts", [])[:20]
    contracts_table = [
        {
            "Vendor":    c.get("vendor", "")[:35],
            "Amount":    c.get("amount_formatted", ""),
            "Category":  c.get("category", "").replace("_", " ").title(),
            "Date":      c.get("date", ""),
            "District":  c.get("council_district", ""),
        }
        for c in contracts
    ]

    no_data_msg = dbc.Alert(
        [html.I(className="bi bi-info-circle me-2"),
         "No analytics data. Run: ",
         html.Code("python scripts/run_analytics.py")],
        color="info",
        className="d-flex align-items-center",
    ) if not _analytics else None

    return html.Div([

        # Page title
        html.Div([
            html.H2("Analytics Dashboard",
                    style={"fontWeight": "700", "fontSize": "1.5rem",
                           "color": TEXT, "marginBottom": "2px"}),
            html.P("Pre-computed metrics from Denver City Council records",
                   style={"color": MUTED, "fontSize": ".88rem", "marginBottom": 0}),
        ], className="mb-4"),

        no_data_msg or html.Div([

            # ── KPI Cards ────────────────────────────────────
            dbc.Row([
                dbc.Col(html.Div([
                    html.Div("Total Votes", className="kpi-label"),
                    html.Div(f"{total_votes:,}" if isinstance(total_votes, int)
                             else str(total_votes), className="kpi-value"),
                    html.Div("recorded in council minutes", className="kpi-sub"),
                ], className="kpi-card"), width=6, lg=3, className="mb-3"),
                dbc.Col(html.Div([
                    html.Div("Contested", className="kpi-label"),
                    html.Div(f"{contested:,}" if isinstance(contested, int)
                             else str(contested), className="kpi-value"),
                    html.Div("votes with at least one Nay", className="kpi-sub"),
                ], className="kpi-card"), width=6, lg=3, className="mb-3"),
                dbc.Col(html.Div([
                    html.Div("Top Dissenter", className="kpi-label"),
                    html.Div(top_name, className="kpi-value",
                             style={"fontSize": "1.5rem"}),
                    html.Div(f"{top_nays} Nay votes · {top_rate} rate", className="kpi-sub"),
                ], className="kpi-card"), width=6, lg=3, className="mb-3"),
                dbc.Col(html.Div([
                    html.Div("Total Spend", className="kpi-label"),
                    html.Div(total_spend, className="kpi-value",
                             style={"fontSize": "1.4rem"}),
                    html.Div(f"{total_ctracts:,} contracts tracked"
                             if isinstance(total_ctracts, int)
                             else str(total_ctracts), className="kpi-sub"),
                ], className="kpi-card"), width=6, lg=3, className="mb-3"),
            ], className="mb-2"),

            # ── Year filter (CHANGE 3: single-select) ─────────
            html.Div([
                html.Span([html.I(className="bi bi-calendar3 me-1"), "Filter by year:"],
                          style={"fontSize": ".78rem", "fontWeight": "600",
                                 "color": MUTED, "whiteSpace": "nowrap"}),
                dcc.Dropdown(
                    id="year-filter",
                    options=year_filter_opts,
                    value="all",
                    multi=False,
                    placeholder="All Years",
                    clearable=False,
                    style={"fontSize": ".82rem", "minWidth": "180px", "flex": "1"},
                ),
            ], className="d-flex align-items-center gap-3 mb-4",
               style={"background": "white", "borderRadius": "12px",
                      "padding": "12px 16px",
                      "boxShadow": "0 1px 3px rgba(0,0,0,.06)"}),

            # ── Voting Patterns ───────────────────────────────
            html.Div([
                html.Div("Voting Patterns by Council Member", className="section-title"),
                dbc.Row([
                    dbc.Col([
                        dcc.Graph(
                            id="vote-chart",
                            figure=_vote_chart(v) if v else go.Figure(),
                            config={"displayModeBar": False},
                            style={"height": "340px"},
                        ),
                    ], width=12, lg=7),
                    dbc.Col([
                        dash_table.DataTable(
                            id="vote-table",
                            data=vote_table_data,
                            columns=[{"name": k, "id": k}
                                     for k in ["Member","Total","Aye","Nay","Absent","Dissent"]],
                            sort_action="native",
                            style_table={"overflowX": "auto", "fontSize": ".83rem"},
                            style_header={"background": "#F9FAFB", "fontWeight": "600",
                                          "fontSize": ".72rem", "color": MUTED,
                                          "textTransform": "uppercase",
                                          "letterSpacing": ".05em",
                                          "border": "none",
                                          "borderBottom": f"1px solid {BORDER}"},
                            style_cell={"fontFamily": "Inter, system-ui, sans-serif",
                                        "border": "none",
                                        "borderBottom": f"1px solid {BORDER}",
                                        "padding": "8px 10px"},
                            style_data_conditional=[
                                {"if": {"filter_query": f"{{Member}} = '{top_name}'"},
                                 "background": "#FFFBF5", "fontWeight": "600"},
                            ],
                            page_size=12,
                        ),
                    ], width=12, lg=5),
                ], className="g-3"),
            ], className="section-card"),

            # ── Most Contested ────────────────────────────────
            html.Div([
                html.Div("Most Contested Votes", className="section-title"),
                html.Div(
                    [_contested_card(b, rank=i+1) for i, b in enumerate(contested_bills)]
                    or [html.P("No contested vote data.", style={"color": MUTED})],
                    id="contested-votes-list",
                ),
            ], className="section-card"),

            # ── Spending by Category ──────────────────────────
            html.Div([
                html.Div("City Spending by Category", className="section-title"),
                # Category pills
                html.Div([
                    html.Button(
                        "All", id="cat-all",
                        n_clicks=0, className="cat-pill active me-1 mb-1",
                    ),
                    *[html.Button(
                        cat_labels.get(c, c.replace("_", " ").title()),
                        id={"type": "cat-pill", "index": c},
                        n_clicks=0,
                        className="cat-pill me-1 mb-1",
                    ) for c in cats_available],
                ], className="mb-3"),
                dbc.Row([
                    dbc.Col([
                        dcc.Graph(
                            id="spending-chart",
                            figure=_spending_bar_chart(f) if f else go.Figure(),
                            config={"displayModeBar": False},
                        ),
                    ], width=12, lg=7),
                    dbc.Col([
                        dash_table.DataTable(
                            id="contracts-table",
                            data=contracts_table,
                            columns=[{"name": k, "id": k}
                                     for k in ["Vendor","Amount","Category","Date","District"]],
                            sort_action="native",
                            page_size=10,
                            style_table={"overflowX": "auto", "fontSize": ".83rem"},
                            style_header={"background": "#F9FAFB", "fontWeight": "600",
                                          "fontSize": ".72rem", "color": MUTED,
                                          "textTransform": "uppercase",
                                          "letterSpacing": ".05em",
                                          "border": "none",
                                          "borderBottom": f"1px solid {BORDER}"},
                            style_cell={"fontFamily": "Inter, system-ui, sans-serif",
                                        "border": "none",
                                        "borderBottom": f"1px solid {BORDER}",
                                        "padding": "8px 10px",
                                        "overflow": "hidden",
                                        "textOverflow": "ellipsis",
                                        "maxWidth": "160px"},
                        ),
                    ], width=12, lg=5),
                ], className="g-3"),
            ], className="section-card"),

            # ── Monthly Trend ─────────────────────────────────
            html.Div([
                html.Div("Monthly Spending Trend", className="section-title"),
                dcc.Graph(
                    id="monthly-chart",
                    figure=_monthly_chart(f) if f else go.Figure(),
                    config={"displayModeBar": False},
                    style={"height": "220px"},
                ),
            ], className="section-card"),

            # ── District Breakdown ────────────────────────────
            html.Div([
                html.Div("Spending by Council District", className="section-title"),
                dcc.Graph(
                    id="district-chart",
                    figure=_district_chart(f) if f else go.Figure(),
                    config={"displayModeBar": False},
                ),
            ], className="section-card"),

        ]),

        # CHANGE 1: Static coverage line (replaced Dataset Management section)
        html.Div([
            html.Hr(style={"borderColor": BORDER, "margin": "8px 0 16px"}),
            html.Span([
                "📅 Coverage: 2020–2026 · CityCouncil · ",
                html.Strong(f"{_init_coverage.get('total_chunks', 0):,}"),
                " chunks indexed",
            ], style={"fontSize": ".82rem", "color": MUTED}),
        ], className="mb-5"),

    ], className="analytics-page")


# ── TRACKER LAYOUT ────────────────────────────────────────────

def tracker_layout():
    return html.Div([

        html.Div([
            html.H2("Topic Tracker",
                    style={"fontWeight": "700", "fontSize": "1.5rem",
                           "color": TEXT, "marginBottom": "4px"}),
            html.P("Follow council topics and get the latest updates from meeting minutes.",
                   style={"color": MUTED, "fontSize": ".88rem", "marginBottom": 0}),
        ], className="mb-4"),

        # Add topic input
        html.Div([
            html.Div([
                dbc.Input(
                    id="topic-input",
                    placeholder="Add a topic to track (e.g. Affordable Housing)…",
                    type="text",
                    style={"fontSize": ".9rem", "borderRadius": "10px 0 0 10px",
                           "border": f"1px solid {BORDER}", "flex": "1"},
                ),
                dbc.Button(
                    "+ Add",
                    id="add-topic-btn",
                    n_clicks=0,
                    style={
                        "background": f"linear-gradient(135deg, {AMBER} 0%, {AMBER_DARK} 100%)",
                        "border": "none",
                        "color": "white",
                        "fontWeight": "600",
                        "borderRadius": "0 10px 10px 0",
                        "padding": "8px 20px",
                    },
                ),
            ], className="d-flex mb-3"),
        ]),

        # Starter topics (shown when list is empty)
        html.Div(id="starter-topics", className="mb-4"),

        # Topics list
        html.Div(id="topics-list"),

    ], className="tracker-page")


# ── ROOT LAYOUT ───────────────────────────────────────────────

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    dcc.Store(id="active-collection",  data=_initial_store),
    dcc.Store(id="chunks-store",       data=None),
    dcc.Store(id="saved-topics",       storage_type="local",   data=[]),
    dcc.Store(id="last-visit",         storage_type="local",   data=None),
    dcc.Store(id="chat-history",       storage_type="session", data=[]),

    html.Div([

        # ── Sidebar ──────────────────────────────────────────
        html.Div([
            # Toggle
            html.Button(
                html.I(className="bi bi-layout-sidebar", id="sidebar-toggle-icon"),
                id="sidebar-toggle",
                n_clicks=0,
                className="sidebar-toggle-btn mt-2",
                style={"justifyContent": "flex-end"},
            ),
            # Logo
            html.A([
                html.Span("🏛️", className="sidebar-logo-icon"),
                html.Span("UrbanInfoGPT", className="sidebar-logo-text"),
            ], href="/chat", className="sidebar-logo"),

            # Nav
            html.Div([
                html.A([
                    html.I(className="bi bi-chat-text nav-icon"),
                    html.Span("Chat", className="nav-label"),
                ], href="/chat", id="nav-chat", className="nav-item active"),
                html.A([
                    html.I(className="bi bi-bar-chart nav-icon"),
                    html.Span("Analytics", className="nav-label"),
                ], href="/analytics", id="nav-analytics", className="nav-item"),
                # CHANGE 2: Tracker nav item (replaces Settings)
                html.A([
                    html.I(className="bi bi-bell nav-icon"),
                    html.Span("Tracker", className="nav-label"),
                ], href="/tracker", id="nav-tracker", className="nav-item"),
            ], className="nav-section"),

            # Footer: chunk count
            html.Div([
                html.I(className="bi bi-database me-1"),
                html.Span(id="sidebar-chunk-count",
                          children=f"{_init_coverage.get('total_chunks', 0):,} chunks"),
            ], className="sidebar-footer"),

        ], id="sidebar"),

        # ── Page content ──────────────────────────────────────
        html.Div(id="page-content"),

    ], style={"display": "flex", "minHeight": "100vh"}),

], style={"fontFamily": "Inter, system-ui, sans-serif"})


# ── CALLBACKS ─────────────────────────────────────────────────

# 1. URL routing
@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
)
def route(pathname):
    if pathname in [None, "/", "/chat"]:
        return chat_layout()
    if pathname == "/analytics":
        return analytics_layout()
    if pathname == "/tracker":
        return tracker_layout()
    return chat_layout()


# 2. Sidebar toggle (CSS class only — no re-render)
@app.callback(
    Output("sidebar", "className"),
    Input("sidebar-toggle", "n_clicks"),
    State("sidebar", "className"),
    prevent_initial_call=True,
)
def toggle_sidebar(_, current):
    return "collapsed" if "collapsed" not in (current or "") else ""


# 3. Active nav highlight (CHANGE 2: added nav-tracker)
@app.callback(
    Output("nav-chat",      "className"),
    Output("nav-analytics", "className"),
    Output("nav-tracker",   "className"),
    Input("url", "pathname"),
)
def nav_active(pathname):
    on_analytics = pathname == "/analytics"
    on_tracker   = pathname == "/tracker"
    on_chat      = not on_analytics and not on_tracker
    return (
        "nav-item" + (" active" if on_chat      else ""),
        "nav-item" + (" active" if on_analytics else ""),
        "nav-item" + (" active" if on_tracker   else ""),
    )


# 4. Coverage → sidebar chunk count only
@app.callback(
    Output("sidebar-chunk-count", "children"),
    Input("active-collection", "data"),
)
def update_coverage(_):
    total = pipeline.get_coverage().get("total_chunks", 0)
    return f"{total:,} chunks"


# 5. Fill question from example
@app.callback(
    Output("question-input", "value"),
    Input({"type": "example", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def fill_example(_):
    triggered = ctx.triggered_id
    if triggered and isinstance(triggered, dict):
        return EXAMPLE_QUESTIONS[triggered["index"]]
    return ""


# 6. Filter collapse toggle
@app.callback(
    Output("filter-collapse-area", "is_open"),
    Output("filter-toggle-btn",    "children"),
    Input("filter-toggle-btn", "n_clicks"),
    State("filter-collapse-area", "is_open"),
    prevent_initial_call=True,
)
def toggle_filter(_, is_open):
    if is_open:
        return False, [html.I(className="bi bi-sliders me-1"), "Filters"]
    return True, [html.I(className="bi bi-sliders me-1"), "Hide Filters"]


# 7. Clear filters
@app.callback(
    Output("filter-start",   "value"),
    Output("filter-end",     "value"),
    Output("filter-types",   "value"),
    Output("filter-content", "value"),
    Input("btn-clear-filters", "n_clicks"),
    prevent_initial_call=True,
)
def clear_filters(_):
    return None, None, None, ""


# 8. Run query
@app.callback(
    Output("answer-panel",          "children"),
    Output("sources-panel",         "children"),
    Output("chunks-store",          "data"),
    Output("show-more-btn",         "style"),
    Output("show-more-btn",         "children"),
    Output("show-more-btn",         "n_clicks"),
    Output("search-strategy-panel", "children"),
    Output("chat-history",          "data"),
    Input("ask-button", "n_clicks"),
    State("question-input",   "value"),
    State("filter-start",     "value"),
    State("filter-end",       "value"),
    State("filter-types",     "value"),
    State("filter-content",   "value"),
    State("active-collection","data"),
    State("chat-history",     "data"),
    prevent_initial_call=True,
)
def run_query(_, question, f_start, f_end, f_types, f_content, coll_data, history):
    _btn_hidden = {"display": "none"}
    _btn_label  = [html.I(className="bi bi-chevron-down me-1"), "Show more sources"]

    if not question or not question.strip():
        return (
            dbc.Alert([html.I(className="bi bi-exclamation-circle me-2"),
                       "Enter a question above."],
                      color="warning", className="d-flex align-items-center"),
            "", None, _btn_hidden, _btn_label, 0, "", history or [],
        )

    coll_name = (coll_data or {}).get("collection_name")
    if not coll_name:
        return (
            dbc.Alert([html.I(className="bi bi-info-circle me-2"),
                       "No data indexed. Run ",
                       html.Code("python scripts/index_history.py"),
                       " to get started."],
                      color="info", className="d-flex align-items-center"),
            "", None, _btn_hidden, _btn_label, 0, "", history or [],
        )

    chunks = refined_search(
        question, n_results=10,
        collection_name=coll_name,
        date_from=f_start  or None,
        date_to=f_end      or None,
        meeting_types=f_types or None,
        content_type=f_content or None,
    )
    refined_queries = get_last_refined_queries()

    # CHANGE 6: Year range suggestion banner
    suggestion_banner = None
    if (f_start or f_end) and (not chunks or (chunks[0].get("score", 0) < 0.15)):
        try:
            all_time_chunks = refined_search(
                question, n_results=3, collection_name=coll_name
            )
            if all_time_chunks and all_time_chunks[0].get("score", 0) > 0.25:
                result_years = sorted(set(
                    c["date"][:4]
                    for c in all_time_chunks
                    if c.get("score", 0) > 0.20 and c.get("date", "")
                ))
                if result_years:
                    suggestion_banner = dbc.Alert(
                        [
                            "💡 Better results found outside your date filter. Try expanding to include: ",
                            html.Strong(", ".join(result_years)),
                            " — clear your date filters and search again.",
                        ],
                        color="warning",
                        className="mb-3",
                    )
        except Exception:
            pass

    if not chunks:
        no_results = dbc.Alert("No results found. Try broadening your filters.", color="warning")
        answer_out = html.Div([suggestion_banner, no_results]) if suggestion_banner else no_results
        return answer_out, "", None, _btn_hidden, _btn_label, 0, "", history or []

    rag_filters = {
        "collection_name": coll_name,
        "date_from":       f_start or None,
        "date_to":         f_end   or None,
        "meeting_types":   f_types or None,
    }
    analytics_ctx, qtype = get_analytics_context(question, _analytics,
                                                  filters=rag_filters) \
        if _analytics else (None, "rag")

    answer, chunks, from_cache = get_answer(
        question, chunks,
        analytics_context=analytics_ctx,
        filters=rag_filters,
    )

    # Badges
    source_badge = (
        dbc.Badge("📊 Analytics + RAG", color="info",   className="me-1", style={"fontSize": ".68rem"})
        if analytics_ctx else
        dbc.Badge("🔍 Semantic Search",  color="light",
                  text_color="secondary", className="me-1",
                  style={"fontSize": ".68rem", "border": f"1px solid {BORDER}"})
    )
    cache_badge = (
        dbc.Badge([html.I(className="bi bi-lightning-charge-fill me-1"), "Cached"],
                  color="warning", className="me-1", style={"fontSize": ".68rem"})
        if from_cache else None
    )

    active_filters = []
    if f_start or f_end:   active_filters.append(f"{f_start or '?'} → {f_end or '?'}")
    if f_types:            active_filters.append(f_types[0] if len(f_types) == 1 else f"{len(f_types)} types")
    if f_content:          active_filters.append(f_content)
    filter_badge = (
        dbc.Badge([html.I(className="bi bi-funnel me-1"), " · ".join(active_filters)],
                  color="secondary", className="me-1",
                  style={"fontSize": ".68rem", "fontWeight": "400"})
        if active_filters else None
    )

    answer_card = html.Div([
        suggestion_banner or "",
        html.Div([
            html.Div([
                html.I(className="bi bi-robot me-2", style={"color": AMBER}),
                html.Span("Answer", style={"fontWeight": "600", "color": TEXT,
                                            "fontSize": ".9rem"}),
                source_badge, cache_badge, filter_badge,
            ], className="answer-header"),
            html.Div([
                dcc.Markdown(answer, style={"fontSize": ".93rem", "lineHeight": "1.75",
                                            "color": TEXT}),
            ], className="answer-body"),
        ], className="answer-card"),
    ])

    show_relevant = chunks and chunks[0]["score"] > 0.1
    sources       = _render_source_grid(chunks[:3]) if show_relevant else ""
    strategy      = _search_strategy_panel(question, refined_queries)

    extra = len(chunks) - 3
    if show_relevant and extra > 0:
        btn_style = {"fontSize": ".78rem", "color": MUTED, "paddingLeft": 0}
        btn_label = [html.I(className="bi bi-chevron-down me-1"),
                     f"Show {extra} more source{'s' if extra != 1 else ''}"]
    else:
        btn_style = _btn_hidden
        btn_label = _btn_label

    # CHANGE 5: Update chat history (deduplicate, keep latest first, cap at 5)
    new_history = [question] + [h for h in (history or []) if h != question]
    new_history = new_history[:5]

    return answer_card, sources, chunks, btn_style, btn_label, 0, strategy, new_history


# 9. Show more / fewer sources
@app.callback(
    Output("sources-panel", "children", allow_duplicate=True),
    Output("show-more-btn", "children", allow_duplicate=True),
    Input("show-more-btn", "n_clicks"),
    State("chunks-store", "data"),
    prevent_initial_call=True,
)
def toggle_sources(n_clicks, chunks_data):
    if not n_clicks:
        return dash.no_update, dash.no_update
    chunks = chunks_data or []
    if not chunks:
        return dash.no_update, dash.no_update

    if n_clicks % 2 == 1:
        visible   = chunks
        btn_label = [html.I(className="bi bi-chevron-up me-1"), "Show fewer sources"]
    else:
        visible   = chunks[:3]
        extra     = len(chunks) - 3
        btn_label = [html.I(className="bi bi-chevron-down me-1"),
                     f"Show {extra} more source{'s' if extra != 1 else ''}"]

    return _render_source_grid(visible), btn_label


# 10. Year filter → update all charts (CHANGE 3: single-select value is a string)
@app.callback(
    Output("spending-chart",       "figure",   allow_duplicate=True),
    Output("contracts-table",      "data",     allow_duplicate=True),
    Output("monthly-chart",        "figure",   allow_duplicate=True),
    Output("district-chart",       "figure",   allow_duplicate=True),
    Output("contested-votes-list", "children", allow_duplicate=True),
    Output("vote-chart",           "figure",   allow_duplicate=True),
    Output("vote-table",           "data",     allow_duplicate=True),
    Input("year-filter", "value"),
    prevent_initial_call=True,
)
def filter_by_year(selected_year):
    if not _analytics:
        return go.Figure(), [], go.Figure(), go.Figure(), [], go.Figure(), []

    f   = _analytics.get("financials") or {}
    v   = _analytics.get("votes")      or {}

    # Normalize: "all" or None means no filter
    yr  = selected_year if (selected_year and selected_year != "all") else None

    # ── Filtered contracts ────────────────────────────────────
    all_contracts = f.get("contracts", [])
    filtered      = [c for c in all_contracts if not yr or c.get("date", "")[:4] == yr]

    # Rebuild by_category
    by_cat: dict = defaultdict(lambda: {"total": 0.0, "count": 0})
    for c in filtered:
        by_cat[c["category"]]["total"] += c["amount"]
        by_cat[c["category"]]["count"] += 1
    fin_filtered = {"summary": {"by_category": {
        k: {"total": v2["total"], "count": v2["count"],
            "formatted": f"${v2['total']/1_000_000:.1f}M"}
        for k, v2 in sorted(by_cat.items(), key=lambda x: -x[1]["total"])
    }}, "contracts": filtered}

    # Rebuild monthly
    by_month: dict = defaultdict(float)
    for c in filtered:
        by_month[c["date"][:7]] += c["amount"]
    fin_filtered["summary"]["monthly_spending"] = [
        {"month": k, "total": round(v2, 2), "formatted": f"${v2/1_000_000:.1f}M"}
        for k, v2 in sorted(by_month.items())
    ]

    # Rebuild by_district
    by_dist: dict = defaultdict(float)
    for c in filtered:
        by_dist[c.get("council_district", "citywide")] += c["amount"]
    fin_filtered["summary"]["by_district"] = {k: round(v2, 2) for k, v2 in by_dist.items()}

    spending_fig   = _spending_bar_chart(fin_filtered)
    monthly_fig    = _monthly_chart(fin_filtered)
    district_fig   = _district_chart(fin_filtered)

    contracts_rows = [
        {
            "Vendor":   c.get("vendor", "")[:35],
            "Amount":   c.get("amount_formatted", ""),
            "Category": c.get("category", "").replace("_", " ").title(),
            "Date":     c.get("date", ""),
            "District": c.get("council_district", ""),
        }
        for c in filtered[:20]
    ]

    # ── Vote chart + table (CHANGE 3) ────────────────────────
    vote_fig  = _vote_chart(v, year=yr or "all")
    vote_rows = _vote_table_data(v, year=yr or "all")

    # ── Filtered contested votes ──────────────────────────────
    all_bills      = v.get("summary", {}).get("most_contested_bills", [])
    filtered_bills = [b for b in all_bills if not yr or b.get("date", "")[:4] == yr][:10]

    contested_children = (
        [_contested_card(b, rank=i+1) for i, b in enumerate(filtered_bills)]
        or [html.P("No contested votes for selected year.", style={"color": MUTED})]
    )

    return spending_fig, contracts_rows, monthly_fig, district_fig, contested_children, vote_fig, vote_rows


# 11. Render history pills (CHANGE 5)
@app.callback(
    Output("history-pills-section", "children"),
    Input("chat-history", "data"),
)
def render_history_pills(history):
    if not history or len(history) < 1:
        return ""
    return html.Div([
        html.Span("Recent questions:",
                  style={"fontSize": ".75rem", "color": MUTED,
                         "fontWeight": "500", "marginRight": "8px",
                         "whiteSpace": "nowrap"}),
        *[
            html.Button(
                f"🕐 {q[:40]}{'…' if len(q) > 40 else ''}",
                id={"type": "history-pill", "index": i},
                n_clicks=0,
                className="history-pill me-2 mb-1",
            )
            for i, q in enumerate(history[:5])
        ],
    ], className="d-flex flex-wrap align-items-center mb-3")


# 12. History pill click → fill input and trigger ask (CHANGE 5)
@app.callback(
    Output("question-input", "value",      allow_duplicate=True),
    Output("ask-button",     "n_clicks",   allow_duplicate=True),
    Input({"type": "history-pill", "index": ALL}, "n_clicks"),
    State("chat-history", "data"),
    State("ask-button",   "n_clicks"),
    prevent_initial_call=True,
)
def history_pill_click(pill_clicks, history, current_ask_clicks):
    if not any(pill_clicks):
        return dash.no_update, dash.no_update
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return dash.no_update, dash.no_update
    idx = triggered["index"]
    history = history or []
    if idx >= len(history):
        return dash.no_update, dash.no_update
    return history[idx], (current_ask_clicks or 0) + 1


# 13. Tracker: add topic (CHANGE 2)
@app.callback(
    Output("saved-topics", "data",    allow_duplicate=True),
    Output("topic-input",  "value",   allow_duplicate=True),
    Input("add-topic-btn", "n_clicks"),
    Input({"type": "starter-topic", "index": ALL}, "n_clicks"),
    State("topic-input",   "value"),
    State("saved-topics",  "data"),
    prevent_initial_call=True,
)
def add_topic(add_clicks, starter_clicks, input_val, saved):
    saved = saved or []
    triggered = ctx.triggered_id

    if triggered == "add-topic-btn":
        topic = (input_val or "").strip()
        if not topic or topic in saved:
            return saved, ""
        return saved + [topic], ""

    if isinstance(triggered, dict) and triggered.get("type") == "starter-topic":
        idx   = triggered["index"]
        topic = STARTER_TOPICS[idx] if idx < len(STARTER_TOPICS) else None
        if not topic or topic in saved:
            return saved, dash.no_update
        return saved + [topic], dash.no_update

    return saved, dash.no_update


# 14. Tracker: remove topic (CHANGE 2)
@app.callback(
    Output("saved-topics", "data", allow_duplicate=True),
    Input({"type": "remove-topic", "index": ALL}, "n_clicks"),
    State("saved-topics", "data"),
    prevent_initial_call=True,
)
def remove_topic(remove_clicks, saved):
    if not any(remove_clicks):
        return dash.no_update
    saved     = saved or []
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return dash.no_update
    idx = triggered["index"]
    if idx >= len(saved):
        return saved
    return [t for i, t in enumerate(saved) if i != idx]


# 15. Tracker: render topics + update last-visit (CHANGE 2)
@app.callback(
    Output("topics-list",   "children"),
    Output("starter-topics","children"),
    Output("last-visit",    "data"),
    Input("saved-topics",   "data"),
    Input("url",            "pathname"),
    prevent_initial_call=True,
)
def render_topics(saved, pathname):
    from datetime import datetime
    saved = saved or []

    # Only update last-visit when on tracker page
    last_visit = datetime.utcnow().isoformat() if pathname == "/tracker" else dash.no_update

    # Show starter pills when list is empty
    if not saved:
        starter_section = html.Div([
            html.P("Suggested topics to track:",
                   style={"fontSize": ".78rem", "color": MUTED,
                          "fontWeight": "600", "marginBottom": "10px"}),
            html.Div([
                html.Button(
                    topic,
                    id={"type": "starter-topic", "index": i},
                    n_clicks=0,
                    className="starter-pill me-2 mb-2",
                )
                for i, topic in enumerate(STARTER_TOPICS)
            ], className="d-flex flex-wrap"),
        ])
        return "", starter_section, last_visit

    # Hide starter section when topics exist
    starter_section = ""

    topic_cards = []
    for idx, topic in enumerate(saved):
        # Search for recent chunks on this topic
        try:
            topic_chunks = refined_search(
                topic, n_results=8,
                date_from="2020-01-01",
                date_to="2026-12-31",
            )
        except Exception:
            topic_chunks = []

        # Filter for resolution/vote chunks and take 3 most recent
        relevant = [
            c for c in (topic_chunks or [])
            if c.get("content_type") in ("resolution", "vote") or c.get("score", 0) > 0.2
        ][:3]

        mini_timeline = []
        for c in relevant:
            mini_timeline.append(html.Div([
                html.Span(c.get("date", "")[:10],
                          style={"fontSize": ".72rem", "fontWeight": "600",
                                 "color": NAVY, "marginRight": "8px",
                                 "whiteSpace": "nowrap"}),
                html.Span(c.get("text", "")[:120] + ("…" if len(c.get("text", "")) > 120 else ""),
                          style={"fontSize": ".78rem", "color": "#4B5563",
                                 "lineHeight": "1.4"}),
            ], style={"display": "flex", "gap": "8px", "marginBottom": "8px",
                      "paddingBottom": "8px", "borderBottom": f"1px solid {BORDER}"}))

        if not mini_timeline:
            mini_timeline = [
                html.P("No recent council activity found for this topic.",
                       style={"fontSize": ".78rem", "color": MUTED, "marginBottom": 0}),
            ]

        topic_cards.append(html.Div([
            html.Div([
                html.Span(topic,
                          style={"fontWeight": "700", "fontSize": "1rem", "color": TEXT}),
                html.Button(
                    html.I(className="bi bi-x"),
                    id={"type": "remove-topic", "index": idx},
                    n_clicks=0,
                    style={"background": "none", "border": "none", "cursor": "pointer",
                           "color": MUTED, "fontSize": ".9rem", "padding": "0 4px"},
                ),
            ], className="d-flex justify-content-between align-items-center mb-3"),
            html.Div(mini_timeline),
        ], className="tracker-card mb-3"))

    return html.Div(topic_cards), starter_section, last_visit


# ── RUN ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nUrbanInfoGPT Dashboard starting...")
    print("Open your browser at: http://127.0.0.1:8050\n")
    app.run(debug=True)
