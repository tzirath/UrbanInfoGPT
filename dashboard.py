import warnings
warnings.filterwarnings("ignore")

import sys
import os
import json
from collections import defaultdict

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
    showlegend=True,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                font=dict(size=10)),
)


def _vote_chart(votes_data):
    members = votes_data.get("members", {})
    ranked  = sorted(members.items(), key=lambda x: x[1]["nay_count"], reverse=True)[:12]
    if not ranked:
        return go.Figure()
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
    fig.update_layout(**_CHART_BASE, barmode="stack", height=340,
                      xaxis=dict(showgrid=True, gridcolor="#F3F4F6", zeroline=False),
                      yaxis=dict(showgrid=False))
    return fig


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
                html.Div([
                    html.Span(html.I(className="bi bi-plus-circle"),
                              style={"fontSize": "1.1rem", "color": MUTED, "cursor": "pointer"}),
                    html.Div([
                        dbc.Button(
                            [html.I(className="bi bi-send-fill me-2"), "Ask"],
                            id="ask-button",
                            className="submit-btn",
                        ),
                    ]),
                ], className="d-flex justify-content-between align-items-center"),
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

    # Vote member table data
    vote_table_data = [
        {
            "Member": name,
            "Total": s["total_votes"],
            "Aye":   s["aye_count"],
            "Nay":   s["nay_count"],
            "Absent": s["absent_count"],
            "Dissent": f"{s['nay_rate']:.1%}",
        }
        for name, s in sorted(members.items(), key=lambda x: x[1]["nay_count"], reverse=True)
        if s["total_votes"] > 0
    ]

    # Most contested bills
    contested_bills = vs.get("most_contested_bills", [])[:10]

    def _contested_card(b):
        total  = b.get("nay_count", 0) + 10  # approximate aye as 10+nay
        nay_pct = int(b["nay_count"] / max(total, 1) * 100)
        return html.Div([
            html.Div([
                html.Span(b.get("resolution") or "Unknown",
                          style={"fontWeight": "700", "fontSize": ".85rem", "color": NAVY}),
                html.Span(
                    dbc.Badge(f"{b['nay_count']} Nay", color="danger",
                              style={"fontSize": ".68rem"}),
                    className="ms-2",
                ),
                html.Span(b.get("date", ""), style={"fontSize": ".73rem", "color": MUTED,
                                                      "marginLeft": "8px"}),
            ], className="d-flex align-items-center"),
            html.Div((b.get("description") or "")[:90],
                     style={"fontSize": ".78rem", "color": MUTED, "marginTop": "3px",
                            "whiteSpace": "nowrap", "overflow": "hidden",
                            "textOverflow": "ellipsis"}),
            html.Div([
                html.Div(style={"flex": f"{100 - nay_pct}", "background": SUCCESS,
                                "height": "100%"}),
                html.Div(style={"flex": str(nay_pct), "background": DANGER,
                                "height": "100%"}),
            ], className="vote-bar"),
        ], className="contested-card")

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

            # ── Voting Patterns ───────────────────────────────
            html.Div([
                html.Div("Voting Patterns by Council Member", className="section-title"),
                dbc.Row([
                    dbc.Col([
                        dcc.Graph(
                            figure=_vote_chart(v) if v else go.Figure(),
                            config={"displayModeBar": False},
                            style={"height": "340px"},
                        ),
                    ], width=12, lg=7),
                    dbc.Col([
                        dash_table.DataTable(
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
                    [_contested_card(b) for b in contested_bills]
                    or [html.P("No contested vote data.", style={"color": MUTED})],
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
                    figure=_monthly_chart(f) if f else go.Figure(),
                    config={"displayModeBar": False},
                    style={"height": "220px"},
                ),
            ], className="section-card"),

            # ── District Breakdown ────────────────────────────
            html.Div([
                html.Div("Spending by Council District", className="section-title"),
                dcc.Graph(
                    figure=_district_chart(f) if f else go.Figure(),
                    config={"displayModeBar": False},
                ),
            ], className="section-card"),

        ]),

        # ── Dataset Management ────────────────────────────────
        html.Div([
            html.Hr(style={"borderColor": BORDER, "margin": "8px 0 16px"}),
            html.Div([
                html.Div(id="index-coverage-text",
                         style={"fontSize": ".82rem", "color": MUTED}),
                dbc.Button(
                    [html.I(className="bi bi-database-fill-add me-1"), "Add data"],
                    id="btn-manage-data", color="light", size="sm",
                    style={"fontSize": ".78rem", "border": f"1px solid {BORDER}",
                           "fontWeight": "500"},
                ),
            ], className="d-flex justify-content-between align-items-center mb-2"),
            dbc.Collapse([
                dbc.Card([dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            html.Label("Start", style={"fontSize": ".76rem", "fontWeight": "600",
                                                        "color": MUTED, "marginBottom": "3px"}),
                            dcc.Dropdown(id="mgmt-start", options=MONTH_OPTIONS,
                                         value="2025-01", clearable=False,
                                         style={"fontSize": ".82rem"}),
                        ], width=3),
                        dbc.Col([
                            html.Label("End", style={"fontSize": ".76rem", "fontWeight": "600",
                                                      "color": MUTED, "marginBottom": "3px"}),
                            dcc.Dropdown(id="mgmt-end", options=MONTH_OPTIONS,
                                         value="2025-12", clearable=False,
                                         style={"fontSize": ".82rem"}),
                        ], width=3),
                        dbc.Col([
                            html.Div([
                                dbc.Button(
                                    [html.I(className="bi bi-clock-history me-1"),
                                     "Last 6 months"],
                                    id="btn-preset-6m", color="light", size="sm",
                                    style={"fontSize": ".78rem",
                                           "border": f"1px solid {BORDER}"}),
                                dbc.Button(
                                    [html.I(className="bi bi-play-fill me-1"), "Index"],
                                    id="index-btn", color="primary", size="sm",
                                    style={"fontSize": ".8rem", "fontWeight": "600"}),
                            ], className="d-flex gap-2 align-items-end",
                               style={"paddingTop": "20px"}),
                        ], width=6),
                    ], className="mb-3 g-3"),
                    # Meeting types
                    html.Div([
                        html.Div([
                            html.Span("Meeting Types", style={"fontWeight": "600",
                                                               "color": NAVY, "fontSize": ".85rem"}),
                            html.Div([
                                dbc.Button("All",  id="btn-select-all", color="light", size="sm",
                                           style={"fontSize": ".72rem", "border": f"1px solid {BORDER}"}),
                                dbc.Button("None", id="btn-clear-all",  color="light", size="sm",
                                           style={"fontSize": ".72rem", "border": f"1px solid {BORDER}"}),
                            ], className="d-flex gap-2"),
                        ], className="d-flex justify-content-between align-items-center mb-2"),
                        dbc.Row([
                            dbc.Col([_group_checklist("types-fullcouncil",
                                                       "Full Council",
                                                       MEETING_GROUPS["Full Council"])], width=3),
                            dbc.Col([_group_checklist("types-committees",
                                                       "Committees",
                                                       MEETING_GROUPS["Committees"])], width=3,
                                     style={"maxHeight": "200px", "overflowY": "auto"}),
                            dbc.Col([_group_checklist("types-workinggroups",
                                                       "Working Groups",
                                                       MEETING_GROUPS["Working Groups"])], width=3),
                            dbc.Col([_group_checklist("types-special",
                                                       "Special Issues",
                                                       MEETING_GROUPS["Special Issues"])], width=3,
                                     style={"maxHeight": "200px", "overflowY": "auto"}),
                        ], className="g-3"),
                    ], style={"background": "#F9FAFB", "borderRadius": "6px",
                               "padding": "12px 14px", "border": f"1px solid {BORDER}"}),
                    html.Div(id="estimate-text",
                             style={"marginTop": "10px", "fontSize": ".8rem", "color": MUTED}),
                    dbc.Collapse([
                        html.Hr(style={"borderColor": BORDER, "margin": "12px 0"}),
                        html.Div(id="manifest-display",
                                 style={"fontSize": ".78rem", "color": MUTED, "marginBottom": "8px"}),
                        html.Pre(id="progress-text",
                                 style={"background": "#1a1f2e", "color": "#E2E8F0",
                                        "borderRadius": "6px", "padding": "10px 14px",
                                        "fontSize": ".78rem", "lineHeight": "1.6",
                                        "height": "150px", "overflowY": "auto",
                                        "fontFamily": "ui-monospace, 'Fira Code', monospace",
                                        "marginBottom": 0, "whiteSpace": "pre-wrap"}),
                    ], id="progress-collapse", is_open=False),
                ], style={"padding": "16px 18px"})],
                         style={"border": f"1px solid {BORDER}", "borderRadius": "8px"}),
            ], id="data-mgmt-collapse", is_open=False),
        ], className="mb-5"),

    ], className="analytics-page")


# ── ROOT LAYOUT ───────────────────────────────────────────────

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    dcc.Store(id="active-collection", data=_initial_store),
    dcc.Store(id="chunks-store",      data=None),
    dcc.Interval(id="poll-interval",  interval=1000, n_intervals=0, disabled=True),

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
                html.A([
                    html.I(className="bi bi-gear nav-icon"),
                    html.Span("Settings", className="nav-label"),
                ], href="#", id="nav-settings",
                   className="nav-item",
                   style={"opacity": ".4", "cursor": "default"}),
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


# 3. Active nav highlight
@app.callback(
    Output("nav-chat",      "className"),
    Output("nav-analytics", "className"),
    Input("url", "pathname"),
)
def nav_active(pathname):
    on_analytics = pathname == "/analytics"
    return (
        "nav-item" + (" active" if not on_analytics else ""),
        "nav-item" + (" active" if on_analytics else ""),
    )


# 4. Coverage → sidebar + analytics coverage text
@app.callback(
    Output("sidebar-chunk-count",  "children"),
    Output("index-coverage-text",  "children"),
    Output("manifest-display",     "children"),
    Input("active-collection", "data"),
)
def update_coverage(coll_data):
    coverage = pipeline.get_coverage()
    total    = coverage.get("total_chunks", 0)
    segments = coverage.get("segments", [])

    chunk_label = f"{total:,} chunks"

    if not total:
        return chunk_label, "No data indexed.", ""

    dates = []
    for seg in segments:
        if seg.get("date_from"): dates.append(seg["date_from"])
        if seg.get("date_to"):   dates.append(seg["date_to"])
    date_str = f"{min(dates)} – {max(dates)}" if dates else "unknown range"

    idx_text = html.Span([
        html.I(className="bi bi-database me-1"),
        f"{total:,} chunks indexed · {date_str}",
    ])
    manifest = html.Div([
        html.Span("Indexed: ", style={"fontWeight": "600", "color": NAVY}),
        *[html.Div(f"• {seg['label']} ({seg['chunks']:,} chunks)",
                   style={"paddingLeft": "12px"})
          for seg in segments],
    ]) if segments else ""

    return chunk_label, idx_text, manifest


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
    Input("ask-button", "n_clicks"),
    State("question-input",   "value"),
    State("filter-start",     "value"),
    State("filter-end",       "value"),
    State("filter-types",     "value"),
    State("filter-content",   "value"),
    State("active-collection","data"),
    prevent_initial_call=True,
)
def run_query(_, question, f_start, f_end, f_types, f_content, coll_data):
    _btn_hidden = {"display": "none"}
    _btn_label  = [html.I(className="bi bi-chevron-down me-1"), "Show more sources"]

    if not question or not question.strip():
        return (
            dbc.Alert([html.I(className="bi bi-exclamation-circle me-2"),
                       "Enter a question above."],
                      color="warning", className="d-flex align-items-center"),
            "", None, _btn_hidden, _btn_label, 0, "",
        )

    coll_name = (coll_data or {}).get("collection_name")
    if not coll_name:
        return (
            dbc.Alert([html.I(className="bi bi-info-circle me-2"),
                       "No data indexed. Go to ",
                       html.Strong("Analytics → Add data"),
                       " to get started."],
                      color="info", className="d-flex align-items-center"),
            "", None, _btn_hidden, _btn_label, 0, "",
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

    if not chunks:
        return (
            dbc.Alert("No results found. Try broadening your filters.", color="warning"),
            "", None, _btn_hidden, _btn_label, 0, "",
        )

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
    ], className="answer-card")

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

    return answer_card, sources, chunks, btn_style, btn_label, 0, strategy


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


# 10. Data management toggle
@app.callback(
    Output("data-mgmt-collapse", "is_open"),
    Output("btn-manage-data",    "children"),
    Input("btn-manage-data", "n_clicks"),
    State("data-mgmt-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_data_mgmt(_, is_open):
    if is_open:
        return False, [html.I(className="bi bi-database-fill-add me-1"), "Add data"]
    return True,  [html.I(className="bi bi-chevron-up me-1"), "Close"]


# 11. Date preset
@app.callback(
    Output("mgmt-start", "value"),
    Output("mgmt-end",   "value"),
    Input("btn-preset-6m", "n_clicks"),
    prevent_initial_call=True,
)
def date_preset(_):
    return "2025-07", "2025-12"


# 12. Meeting type presets
@app.callback(
    Output("types-fullcouncil",   "value"),
    Output("types-committees",    "value"),
    Output("types-workinggroups", "value"),
    Output("types-special",       "value"),
    Input("btn-select-all", "n_clicks"),
    Input("btn-clear-all",  "n_clicks"),
    Input("btn-preset-6m",  "n_clicks"),
    prevent_initial_call=True,
)
def type_presets(*_):
    triggered = ctx.triggered_id
    if triggered == "btn-select-all":
        return (
            [mt for mt, _ in MEETING_GROUPS["Full Council"]],
            [mt for mt, _ in MEETING_GROUPS["Committees"]],
            [mt for mt, _ in MEETING_GROUPS["Working Groups"]],
            [mt for mt, _ in MEETING_GROUPS["Special Issues"]],
        )
    if triggered in ("btn-clear-all", "btn-preset-6m"):
        fc = ["CityCouncil"] if triggered == "btn-preset-6m" else []
        return fc, [], [], []
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update


# 13. Index size estimate
@app.callback(
    Output("estimate-text", "children"),
    Input("types-fullcouncil",   "value"),
    Input("types-committees",    "value"),
    Input("types-workinggroups", "value"),
    Input("types-special",       "value"),
    Input("mgmt-start", "value"),
    Input("mgmt-end",   "value"),
)
def estimate(fc, co, wg, sp, start, end):
    selected = (fc or []) + (co or []) + (wg or []) + (sp or [])
    if not selected:
        return html.Span("Select at least one meeting type.", style={"color": DANGER})
    total_rows = sum(_TYPE_ROWS.get(mt, 0) for mt in selected)
    scale = 1.0
    if start and end:
        sy, sm = map(int, start.split("-"))
        ey, em = map(int, end.split("-"))
        months = max(1, (ey - sy) * 12 + (em - sm) + 1)
        scale  = min(1.0, months / 12)
    est_chunks = int(total_rows * 4 * scale)
    est_min    = max(1, est_chunks // 6000)
    return html.Span([
        html.I(className="bi bi-info-circle me-1"),
        f"{len(selected)} type(s) · ~",
        html.B(f"{est_chunks:,} chunks"),
        f" · ~{est_min} min",
    ])


# 14. Indexing
@app.callback(
    Output("progress-text",     "children"),
    Output("active-collection", "data"),
    Output("poll-interval",     "disabled"),
    Output("index-btn",         "disabled"),
    Output("progress-collapse", "is_open"),
    Input("index-btn",     "n_clicks"),
    Input("poll-interval", "n_intervals"),
    State("mgmt-start",           "value"),
    State("mgmt-end",             "value"),
    State("types-fullcouncil",    "value"),
    State("types-committees",     "value"),
    State("types-workinggroups",  "value"),
    State("types-special",        "value"),
    State("active-collection",    "data"),
    prevent_initial_call=True,
)
def handle_indexing(index_click, n_intervals,
                    start, end, fc, co, wg, sp, current_store):
    triggered = ctx.triggered_id
    if triggered == "index-btn":
        meeting_types = (fc or []) + (co or []) + (wg or []) + (sp or [])
        if not meeting_types:
            return ("Select at least one meeting type.",
                    current_store, True, False, True)
        if not start or not end:
            return ("Select a date range.", current_store, True, False, True)
        state = pipeline.get_progress()
        if state["status"] == "running":
            return ("\n".join(state["lines"][-12:]), current_store, False, True, True)
        pipeline.start_pipeline(meeting_types, start, end)
        return ("Starting…", current_store, False, True, True)

    elif triggered == "poll-interval":
        state = pipeline.get_progress()
        text  = "\n".join(state["lines"][-12:]) if state["lines"] else "…"
        if state["status"] == "done":
            cov   = pipeline.get_coverage()
            store = {"collection_name": pipeline.COLLECTION_NAME,
                     "chunks":          cov["total_chunks"]}
            pipeline.reset()
            return (text, store, True, False, True)
        if state["status"] == "error":
            pipeline.reset()
            return (text, current_store, True, False, True)
        return (text, current_store, False, True, True)

    return dash.no_update, dash.no_update, True, False, dash.no_update


# ── RUN ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nUrbanInfoGPT Dashboard starting...")
    print("Open your browser at: http://127.0.0.1:8050\n")
    app.run(debug=True)
