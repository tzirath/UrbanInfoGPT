import warnings
warnings.filterwarnings("ignore")

import sys
import os
import json
from collections import defaultdict, Counter

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import dash
from dash import Dash, html, dcc, Input, Output, State, ALL, ctx
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

import pipeline
from query import query
from llm import get_answer

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

# ── PALETTE ──────────────────────────────────────────────────
NAVY      = "#1B3A5C"
NAVY_DARK = "#112840"
BLUE      = "#2563EB"
SLATE     = "#64748B"
BG        = "#F8FAFC"
BORDER    = "#E2E8F0"

# ── MEETING TYPE CATALOGUE ───────────────────────────────────
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

# Flat sorted options for the filter dropdown (most records first)
TYPE_FILTER_OPTIONS = sorted(
    [{"label": f"{mt}  ({cnt:,})", "value": mt}
     for grp in MEETING_GROUPS.values() for mt, cnt in grp],
    key=lambda o: -_TYPE_ROWS[o["value"]],
)

EXAMPLE_QUESTIONS = [
    "What contracts were approved for DIA?",
    "Did council vote on affordable housing?",
    "What climate initiatives were discussed?",
    "How did council vote on the 2026 budget?",
    "What did council decide about homeless shelters?",
    "Were there any major zoning changes in 2025?",
]

# ── DATE OPTIONS ─────────────────────────────────────────────
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

_DEFAULT_MGMT_START = "2025-01"
_DEFAULT_MGMT_END   = "2025-12"

# ── STARTUP STATE ────────────────────────────────────────────
_init_coll     = pipeline.get_active_collection_name()
_init_coverage = pipeline.get_coverage()
_initial_store = (
    {"collection_name": _init_coll, "chunks": _init_coverage["total_chunks"]}
    if _init_coll else None
)

# ── STATIC CHARTS (2025 CityCouncil overview) ────────────────
def _load_chart_data():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "raw", "CityCouncil_2025.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    monthly_mtgs  = defaultdict(set)
    monthly_pages = Counter()
    for d in data:
        m = d["date"][:7]
        monthly_mtgs[m].add(d["date"])
        monthly_pages[m] += 1
    topics = {
        "Budget & Finance":  ["budget", "funding", "appropriation", "contract", "revenue"],
        "Zoning & Land Use": ["zoning", "rezoning", "land use", "ordinance", "variance"],
        "Infrastructure":    ["infrastructure", "road", "bridge", "sewer", "water", "park"],
        "Transportation":    ["transit", "bus", "light rail", "rtd", "pedestrian"],
        "Airport (DIA)":     ["airport", "dia", "aviation"],
        "Housing":           ["housing", "affordable", "rental", "eviction", "tenant"],
        "Public Safety":     ["police", "fire", "safety", "crime"],
        "Homelessness":      ["homeless", "shelter", "encampment", "unhoused"],
        "Education":         ["school", "education", "library", "youth"],
        "Environment":       ["climate", "environment", "green", "sustainability"],
    }
    topic_counts = {
        t: sum(1 for d in data if any(kw in d["text"].lower() for kw in kws))
        for t, kws in topics.items()
    }
    return {
        "monthly_mtgs":  {k: len(v) for k, v in sorted(monthly_mtgs.items())},
        "monthly_pages": dict(sorted(monthly_pages.items())),
        "topic_counts":  dict(sorted(topic_counts.items(), key=lambda x: x[1])),
    }

_CHART_DATA = _load_chart_data()

_MONTH_ABBR = {
    "2025-01": "Jan", "2025-02": "Feb", "2025-03": "Mar", "2025-04": "Apr",
    "2025-05": "May", "2025-06": "Jun", "2025-07": "Jul", "2025-08": "Aug",
    "2025-09": "Sep", "2025-10": "Oct", "2025-11": "Nov", "2025-12": "Dec",
}

_CHART_BASE = dict(
    paper_bgcolor="white", plot_bgcolor="white",
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#374151"),
    margin=dict(l=0, r=32, t=36, b=0),
)


def _topic_fig():
    if not _CHART_DATA:
        return go.Figure()
    t, c = list(_CHART_DATA["topic_counts"].keys()), list(_CHART_DATA["topic_counts"].values())
    fig = go.Figure(go.Bar(
        x=c, y=t, orientation="h",
        marker=dict(color=c, colorscale=[[0, "#7CB9E8"], [1, NAVY]]),
        text=c, textposition="outside", textfont=dict(size=11),
        hovertemplate="%{y}: %{x} pages<extra></extra>",
    ))
    fig.update_layout(
        **_CHART_BASE, height=300,
        title=dict(text="Discussion Topics — 2025",
                   font=dict(size=12, color=NAVY), x=0),
        xaxis=dict(title="Pages", showgrid=True, gridcolor="#F1F5F9",
                   showline=False, zeroline=False),
        yaxis=dict(showgrid=False, tickfont=dict(size=10)),
    )
    return fig


def _activity_fig():
    if not _CHART_DATA:
        return go.Figure()
    months   = [_MONTH_ABBR[m] for m in _CHART_DATA["monthly_mtgs"]]
    meetings = list(_CHART_DATA["monthly_mtgs"].values())
    pages    = [_CHART_DATA["monthly_pages"][m] for m in _CHART_DATA["monthly_mtgs"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=months, y=pages, name="Pages", marker_color="#7CB9E8",
                         hovertemplate="%{x}: %{y} pages<extra></extra>"))
    fig.add_trace(go.Scatter(x=months, y=meetings, name="Meetings", yaxis="y2",
                             mode="lines+markers",
                             line=dict(color=NAVY, width=2), marker=dict(size=5, color=NAVY),
                             hovertemplate="%{x}: %{y} meetings<extra></extra>"))
    fig.update_layout(
        **_CHART_BASE, height=300,
        title=dict(text="Monthly Activity — 2025",
                   font=dict(size=12, color=NAVY), x=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=10)),
        xaxis=dict(showgrid=False, showline=False),
        yaxis=dict(title="Pages", showgrid=True, gridcolor="#F1F5F9",
                   zeroline=False, showline=False),
        yaxis2=dict(title="Meetings", overlaying="y", side="right",
                    showgrid=False, zeroline=False, dtick=1, showline=False),
        bargap=0.25,
    )
    return fig


# ── SMALL UI HELPERS ─────────────────────────────────────────

_CTYPE_COLORS = {
    "resolution": "primary",
    "vote":       "success",
    "procedural": "secondary",
    "other":      "light",
}

def _source_card(i, chunk):
    pct        = int(chunk["score"] * 100)
    sem_pct    = int(chunk.get("semantic_score", chunk["score"]) * 100)
    bm25_pct   = int(chunk.get("bm25_score",    0) * 100)
    has_hybrid = chunk.get("bm25_score") is not None and chunk.get("bm25_score", 0) > 0

    score_color = "success" if pct >= 70 else ("warning" if pct >= 50 else "secondary")
    res_num     = chunk.get("resolution_number")
    ctype       = chunk.get("content_type", "other")

    return dbc.Card([
        dbc.CardBody([
            # Header row: source badge + resolution number + content type
            html.Div([
                dbc.Badge(f"Source {i+1}", color="primary",
                          className="me-1", style={"fontSize": "0.68rem"}),
                dbc.Badge(f"Res {res_num}", color="info",
                          className="me-1", style={"fontSize": "0.68rem"})
                if res_num else None,
                dbc.Badge(ctype, color=_CTYPE_COLORS.get(ctype, "light"),
                          text_color="dark" if ctype == "other" else None,
                          style={"fontSize": "0.68rem"}),
            ], className="d-flex flex-wrap gap-1 mb-2"),

            # Date / page / meeting
            html.Div([
                html.Span(chunk["date"],
                          style={"fontSize": "0.76rem", "fontWeight": "600",
                                 "color": NAVY, "marginRight": "8px"}),
                html.Span(f"Page {chunk['page']}",
                          style={"fontSize": "0.76rem", "color": SLATE,
                                 "marginRight": "8px"}),
                html.Span(chunk["meeting"][:28],
                          style={"fontSize": "0.7rem", "color": "#9CA3AF"}),
            ], className="mb-2"),

            # Score row
            html.Div([
                dbc.Badge(f"{pct}%", color=score_color,
                          className="me-1", style={"fontSize": "0.7rem"}),
                html.Span(
                    f"Semantic {sem_pct}%  ·  Keyword {bm25_pct}%"
                    if has_hybrid else f"Semantic {sem_pct}%",
                    style={"fontSize": "0.7rem", "color": SLATE},
                ),
            ], className="d-flex align-items-center mb-2"),

            html.P(chunk["text"][:230] + "…",
                   style={"fontSize": "0.81rem", "color": "#4B5563",
                          "marginBottom": 0, "lineHeight": "1.5"}),
        ], style={"padding": "12px 14px"}),
    ], style={"border": f"1px solid {BORDER}", "borderRadius": "8px",
              "boxShadow": "0 1px 3px rgba(0,0,0,.05)", "height": "100%"})


def _group_checklist(group_id, group_name, types):
    return html.Div([
        html.Div(group_name,
                 style={"fontSize": "0.7rem", "fontWeight": "700", "color": NAVY,
                        "textTransform": "uppercase", "letterSpacing": "0.07em",
                        "marginBottom": "5px", "paddingBottom": "3px",
                        "borderBottom": f"2px solid {NAVY}"}),
        dbc.Checklist(
            id=group_id,
            options=[
                {"label": html.Span([
                    html.Span(mt, style={"fontSize": "0.78rem"}),
                    html.Span(f" ({cnt:,})",
                              style={"fontSize": "0.7rem", "color": SLATE}),
                ]), "value": mt}
                for mt, cnt in types
            ],
            value=["CityCouncil"] if group_id == "types-fullcouncil" else [],
            input_style={"cursor": "pointer"},
            label_style={"marginBottom": "3px", "cursor": "pointer"},
        ),
    ])


# ── APP ──────────────────────────────────────────────────────
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.FLATLY,
        dbc.icons.BOOTSTRAP,
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    ],
)
app.title = "UrbanInfoGPT — Denver City Council"


# ── LAYOUT ───────────────────────────────────────────────────
app.layout = html.Div([

    # ── Header ──────────────────────────────────────────────
    html.Div([
        dbc.Container([
            dbc.Row([
                dbc.Col([
                    html.H1("UrbanInfoGPT",
                            style={"color": "white", "fontWeight": "700",
                                   "fontSize": "1.6rem", "letterSpacing": "-0.3px",
                                   "marginBottom": "2px"}),
                    html.P("AI-powered search across Denver City Council minutes",
                           style={"color": "#94A3B8", "marginBottom": 0,
                                  "fontSize": "0.85rem"}),
                ], className="d-flex flex-column justify-content-center"),
                dbc.Col([
                    html.Div(id="header-coverage",
                             className="d-flex justify-content-end align-items-center"),
                ]),
            ], className="g-0", style={"minHeight": "68px"}),
        ], fluid=True, style={"padding": "0 24px"}),
    ], style={"backgroundColor": NAVY, "borderBottom": f"3px solid {NAVY_DARK}"}),

    # ── Main ────────────────────────────────────────────────
    dbc.Container([

        # ── Hero Search ──────────────────────────────────
        html.Div([

            # Coverage summary
            html.Div(id="coverage-badge", className="text-center mb-4"),

            # Search input
            dbc.InputGroup([
                html.Span([html.I(className="bi bi-search")],
                          className="input-group-text",
                          style={"backgroundColor": "white", "borderRight": "none",
                                 "color": SLATE, "fontSize": "1.1rem",
                                 "paddingLeft": "16px"}),
                dbc.Input(
                    id="question-input",
                    placeholder="Ask anything about Denver City Council meetings…",
                    type="text",
                    style={"borderLeft": "none", "boxShadow": "none",
                           "fontSize": "1rem", "padding": "14px 4px"},
                ),
                dbc.Button(
                    [html.I(className="bi bi-send-fill me-2"), "Ask"],
                    id="ask-button", color="primary",
                    style={"fontWeight": "600", "fontSize": "0.95rem",
                           "paddingLeft": "24px", "paddingRight": "24px",
                           "borderRadius": "0 8px 8px 0"},
                ),
            ], style={"boxShadow": "0 4px 16px rgba(0,0,0,.12)",
                      "borderRadius": "8px", "overflow": "hidden",
                      "border": f"1px solid {BORDER}"}),

            # Example chips
            html.Div([
                html.Span("Try: ", style={"fontSize": "0.8rem", "color": SLATE,
                                          "fontWeight": "500", "marginRight": "4px"}),
                *[
                    html.Button(
                        q,
                        id={"type": "example", "index": i},
                        n_clicks=0,
                        className="me-2 mb-1",
                        style={
                            "cursor": "pointer",
                            "fontSize": "0.76rem",
                            "fontWeight": "500",
                            "color": BLUE,
                            "backgroundColor": "white",
                            "border": f"1px solid {BORDER}",
                            "borderRadius": "20px",
                            "padding": "4px 14px",
                            "lineHeight": "1.5",
                        },
                    )
                    for i, q in enumerate(EXAMPLE_QUESTIONS)
                ],
            ], className="d-flex flex-wrap align-items-center mt-3"),

            # Filter row
            html.Div([
                html.Span([html.I(className="bi bi-funnel me-1"), "Filter:"],
                          style={"fontSize": "0.78rem", "fontWeight": "600",
                                 "color": SLATE, "whiteSpace": "nowrap"}),
                dcc.Dropdown(
                    id="filter-start",
                    options=MONTH_OPTIONS,
                    placeholder="From (any)",
                    clearable=True,
                    style={"width": "132px", "fontSize": "0.82rem",
                           "minWidth": "110px"},
                ),
                html.Span("→", style={"color": SLATE, "fontSize": "0.85rem",
                                      "padding": "0 2px"}),
                dcc.Dropdown(
                    id="filter-end",
                    options=MONTH_OPTIONS,
                    placeholder="To (any)",
                    clearable=True,
                    style={"width": "132px", "fontSize": "0.82rem",
                           "minWidth": "110px"},
                ),
                dcc.Dropdown(
                    id="filter-types",
                    options=TYPE_FILTER_OPTIONS,
                    placeholder="All meeting types",
                    multi=True,
                    clearable=True,
                    style={"flex": "1", "minWidth": "180px",
                           "fontSize": "0.82rem"},
                ),
                dcc.Dropdown(
                    id="filter-content",
                    options=[
                        {"label": "All content",      "value": ""},
                        {"label": "Resolutions only",  "value": "resolution"},
                        {"label": "Votes only",        "value": "vote"},
                        {"label": "Procedural only",   "value": "procedural"},
                    ],
                    value="",
                    clearable=False,
                    style={"width": "148px", "fontSize": "0.82rem",
                           "minWidth": "130px"},
                ),
                dbc.Button(
                    [html.I(className="bi bi-x me-1"), "Clear"],
                    id="btn-clear-filters", color="light", size="sm",
                    style={"fontSize": "0.75rem", "whiteSpace": "nowrap",
                           "border": f"1px solid {BORDER}"},
                ),
            ], className="d-flex align-items-center gap-2 flex-wrap mt-3",
               style={"backgroundColor": "#F1F5F9", "borderRadius": "8px",
                      "padding": "8px 12px", "border": f"1px solid {BORDER}"}),

        ], style={"maxWidth": "780px", "margin": "0 auto",
                  "paddingTop": "52px", "paddingBottom": "24px"}),

        # ── Results ──────────────────────────────────────
        html.Div([
            dcc.Loading(id="loading", type="dot", color=BLUE, children=[
                html.Div(id="answer-panel",  className="mb-3"),
                html.Div(id="sources-panel", className="mb-2"),
            ]),
        ], style={"maxWidth": "900px", "margin": "0 auto"}),

        # ── Context charts ───────────────────────────────
        html.Div([
            html.Hr(style={"borderColor": BORDER, "margin": "32px 0 20px"}),
            html.P("Document Overview — Denver City Council 2025",
                   style={"fontSize": "0.78rem", "fontWeight": "600",
                          "color": SLATE, "textTransform": "uppercase",
                          "letterSpacing": "0.06em", "marginBottom": "16px"}),
            dbc.Row([
                dbc.Col([
                    dbc.Card([
                        dbc.CardBody([
                            dcc.Graph(figure=_topic_fig(),
                                      config={"displayModeBar": False},
                                      style={"height": "300px"}),
                        ], style={"padding": "14px 18px"}),
                    ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
                              "boxShadow": "0 1px 4px rgba(0,0,0,.05)"}),
                ], width=7),
                dbc.Col([
                    dbc.Card([
                        dbc.CardBody([
                            dcc.Graph(figure=_activity_fig(),
                                      config={"displayModeBar": False},
                                      style={"height": "300px"}),
                        ], style={"padding": "14px 18px"}),
                    ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
                              "boxShadow": "0 1px 4px rgba(0,0,0,.05)"}),
                ], width=5),
            ], className="g-3"),
        ], className="mb-4"),

        # ── Data management ──────────────────────────────
        html.Div([
            html.Hr(style={"borderColor": BORDER, "margin": "8px 0 16px"}),

            html.Div([
                html.Div(id="index-coverage-text",
                         style={"fontSize": "0.82rem", "color": SLATE}),
                dbc.Button(
                    [html.I(className="bi bi-database-fill-add me-1"), "Add data"],
                    id="btn-manage-data", color="light", size="sm",
                    style={"fontSize": "0.78rem", "border": f"1px solid {BORDER}",
                           "fontWeight": "500"},
                ),
            ], className="d-flex justify-content-between align-items-center mb-2"),

            dbc.Collapse([
                dbc.Card([
                    dbc.CardBody([

                        # Date range + action buttons
                        dbc.Row([
                            dbc.Col([
                                html.Label("Start", style={"fontSize": "0.76rem",
                                                           "fontWeight": "600",
                                                           "color": SLATE,
                                                           "marginBottom": "3px"}),
                                dcc.Dropdown(id="mgmt-start", options=MONTH_OPTIONS,
                                             value=_DEFAULT_MGMT_START, clearable=False,
                                             style={"fontSize": "0.82rem"}),
                            ], width=3),
                            dbc.Col([
                                html.Label("End", style={"fontSize": "0.76rem",
                                                         "fontWeight": "600",
                                                         "color": SLATE,
                                                         "marginBottom": "3px"}),
                                dcc.Dropdown(id="mgmt-end", options=MONTH_OPTIONS,
                                             value=_DEFAULT_MGMT_END, clearable=False,
                                             style={"fontSize": "0.82rem"}),
                            ], width=3),
                            dbc.Col([
                                html.Div([
                                    dbc.Button(
                                        [html.I(className="bi bi-clock-history me-1"),
                                         "Last 6 months"],
                                        id="btn-preset-6m", color="light", size="sm",
                                        style={"fontSize": "0.78rem",
                                               "border": f"1px solid {BORDER}"}),
                                    dbc.Button(
                                        [html.I(className="bi bi-play-fill me-1"),
                                         "Index"],
                                        id="index-btn", color="primary", size="sm",
                                        style={"fontSize": "0.8rem",
                                               "fontWeight": "600",
                                               "paddingLeft": "20px",
                                               "paddingRight": "20px"}),
                                ], className="d-flex gap-2 align-items-end",
                                   style={"paddingTop": "20px"}),
                            ], width=6),
                        ], className="mb-3 g-3"),

                        # Meeting types
                        html.Div([
                            html.Div([
                                html.Span("Meeting Types",
                                          style={"fontWeight": "600", "color": NAVY,
                                                 "fontSize": "0.85rem"}),
                                html.Div([
                                    dbc.Button("All", id="btn-select-all",
                                               color="light", size="sm",
                                               style={"fontSize": "0.72rem",
                                                      "border": f"1px solid {BORDER}"}),
                                    dbc.Button("None", id="btn-clear-all",
                                               color="light", size="sm",
                                               style={"fontSize": "0.72rem",
                                                      "border": f"1px solid {BORDER}"}),
                                ], className="d-flex gap-2"),
                            ], className="d-flex justify-content-between align-items-center mb-2"),

                            dbc.Row([
                                dbc.Col([_group_checklist("types-fullcouncil",
                                                           "Full Council",
                                                           MEETING_GROUPS["Full Council"])],
                                        width=3),
                                dbc.Col([_group_checklist("types-committees",
                                                           "Committees",
                                                           MEETING_GROUPS["Committees"])],
                                        width=3,
                                        style={"maxHeight": "200px", "overflowY": "auto"}),
                                dbc.Col([_group_checklist("types-workinggroups",
                                                           "Working Groups",
                                                           MEETING_GROUPS["Working Groups"])],
                                        width=3),
                                dbc.Col([_group_checklist("types-special",
                                                           "Special Issues",
                                                           MEETING_GROUPS["Special Issues"])],
                                        width=3,
                                        style={"maxHeight": "200px", "overflowY": "auto"}),
                            ], className="g-3"),
                        ], style={"backgroundColor": BG, "borderRadius": "6px",
                                  "padding": "12px 14px",
                                  "border": f"1px solid {BORDER}"}),

                        # Estimate
                        html.Div(id="estimate-text",
                                 style={"marginTop": "10px", "fontSize": "0.8rem",
                                        "color": SLATE}),

                        # Progress
                        dbc.Collapse([
                            html.Hr(style={"borderColor": BORDER, "margin": "12px 0"}),
                            html.Div(id="manifest-display",
                                     style={"fontSize": "0.78rem", "color": SLATE,
                                            "marginBottom": "8px"}),
                            html.Pre(
                                id="progress-text",
                                style={"backgroundColor": NAVY_DARK, "color": "#E2E8F0",
                                       "borderRadius": "6px", "padding": "10px 14px",
                                       "fontSize": "0.78rem", "lineHeight": "1.6",
                                       "height": "150px", "overflowY": "auto",
                                       "fontFamily": "ui-monospace, 'Fira Code', monospace",
                                       "marginBottom": 0, "whiteSpace": "pre-wrap"},
                            ),
                        ], id="progress-collapse", is_open=False),

                    ], style={"padding": "16px 18px"}),
                ], style={"border": f"1px solid {BORDER}", "borderRadius": "8px",
                          "boxShadow": "0 1px 4px rgba(0,0,0,.05)"}),
            ], id="data-mgmt-collapse", is_open=False),
        ], className="mb-5"),

    ], fluid=True, style={"backgroundColor": BG, "padding": "0 32px",
                          "minHeight": "calc(100vh - 68px)"}),

    # ── Footer ──────────────────────────────────────────────
    html.Div([
        html.P([
            "UrbanInfoGPT · Answers sourced from official ",
            html.A("Denver City Council minutes",
                   href="https://denver.gov/city-council", target="_blank",
                   style={"color": "#94A3B8"}),
            " · Powered by Claude + RAG",
        ], style={"color": "#64748B", "fontSize": "0.76rem",
                  "marginBottom": 0, "textAlign": "center"}),
    ], style={"backgroundColor": NAVY_DARK, "padding": "14px 0",
              "borderTop": "1px solid #0f2035"}),

    # ── Hidden state ────────────────────────────────────────
    dcc.Store(id="active-collection", data=_initial_store),
    dcc.Interval(id="poll-interval", interval=1000, n_intervals=0, disabled=True),

], style={"fontFamily": "Inter, system-ui, sans-serif", "backgroundColor": BG})


# ── CALLBACKS ────────────────────────────────────────────────

# 1. Update coverage display whenever the store changes
@app.callback(
    Output("coverage-badge",     "children"),
    Output("header-coverage",    "children"),
    Output("index-coverage-text","children"),
    Output("manifest-display",   "children"),
    Input("active-collection", "data"),
)
def update_coverage(coll_data):
    coverage = pipeline.get_coverage()
    total    = coverage.get("total_chunks", 0)
    segments = coverage.get("segments", [])

    if not total:
        badge = dbc.Alert([
            html.I(className="bi bi-exclamation-circle me-2"),
            "No data indexed yet — use ",
            html.Strong("Add data"),
            " below to get started.",
        ], color="warning", className="d-inline-flex align-items-center py-2 px-3 mb-0",
           style={"fontSize": "0.85rem", "borderRadius": "20px"})
        hdr   = dbc.Badge("No data loaded", color="warning",
                          className="px-3 py-2", style={"fontSize": "0.75rem"})
        idx_text = "No data indexed."
        manifest = ""
        return badge, hdr, idx_text, manifest

    # Summarise coverage
    all_types = set()
    dates     = []
    for seg in segments:
        all_types.update(seg.get("meeting_types", []))
        if seg.get("date_from"): dates.append(seg["date_from"])
        if seg.get("date_to"):   dates.append(seg["date_to"])

    n_types   = len(all_types)
    date_str  = f"{min(dates)} – {max(dates)}" if dates else "unknown range"
    type_str  = (
        list(all_types)[0] if n_types == 1
        else f"{n_types} meeting types"
    )

    badge = html.Div([
        dbc.Badge([
            html.I(className="bi bi-check-circle-fill me-1"),
            f"{total:,} documents indexed",
        ], color="success", className="me-2 px-3 py-2",
           style={"fontSize": "0.82rem"}),
        html.Span(f"{type_str} · {date_str}",
                  style={"fontSize": "0.82rem", "color": SLATE}),
    ], className="d-flex align-items-center justify-content-center gap-1")

    hdr = dbc.Badge([
        html.I(className="bi bi-database-fill-check me-1"),
        f"{total:,} chunks",
    ], color="success", className="px-3 py-2", style={"fontSize": "0.75rem"})

    idx_text = html.Span([
        html.I(className="bi bi-database me-1"),
        f"{total:,} chunks indexed · {type_str} · {date_str}",
    ])

    manifest = html.Div([
        html.Span("Currently indexed: ",
                  style={"fontWeight": "600", "color": NAVY}),
        *[html.Div(f"• {seg['label']} ({seg['chunks']:,} chunks)",
                   style={"paddingLeft": "12px"})
          for seg in segments],
    ]) if segments else ""

    return badge, hdr, idx_text, manifest


# 2. Fill question from example chip
@app.callback(
    Output("question-input", "value"),
    Input({"type": "example", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def fill_from_example(_):
    triggered = ctx.triggered_id
    if triggered and isinstance(triggered, dict):
        return EXAMPLE_QUESTIONS[triggered["index"]]
    return ""


# 3. Clear filters
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


# 4. Toggle data management panel
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


# 5. Date preset for management panel
@app.callback(
    Output("mgmt-start", "value"),
    Output("mgmt-end",   "value"),
    Input("btn-preset-6m", "n_clicks"),
    prevent_initial_call=True,
)
def apply_date_preset(_):
    return "2025-07", "2025-12"


# 6. Meeting type presets
@app.callback(
    Output("types-fullcouncil",   "value"),
    Output("types-committees",    "value"),
    Output("types-workinggroups", "value"),
    Output("types-special",       "value"),
    Input("btn-select-all",  "n_clicks"),
    Input("btn-clear-all",   "n_clicks"),
    Input("btn-preset-6m",   "n_clicks"),
    prevent_initial_call=True,
)
def apply_type_preset(*_):
    triggered = ctx.triggered_id
    if triggered == "btn-select-all":
        return (
            [mt for mt, _ in MEETING_GROUPS["Full Council"]],
            [mt for mt, _ in MEETING_GROUPS["Committees"]],
            [mt for mt, _ in MEETING_GROUPS["Working Groups"]],
            [mt for mt, _ in MEETING_GROUPS["Special Issues"]],
        )
    if triggered == "btn-clear-all":
        return [], [], [], []
    if triggered == "btn-preset-6m":
        return ["CityCouncil"], [], [], []
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update


# 7. Estimated index size
@app.callback(
    Output("estimate-text", "children"),
    Input("types-fullcouncil",   "value"),
    Input("types-committees",    "value"),
    Input("types-workinggroups", "value"),
    Input("types-special",       "value"),
    Input("mgmt-start", "value"),
    Input("mgmt-end",   "value"),
)
def update_estimate(fc, co, wg, sp, start, end):
    selected = (fc or []) + (co or []) + (wg or []) + (sp or [])
    if not selected:
        return html.Span("Select at least one meeting type.",
                         style={"color": "#EF4444"})
    total_rows = sum(_TYPE_ROWS.get(mt, 0) for mt in selected)
    if start and end:
        sy, sm = map(int, start.split("-"))
        ey, em = map(int, end.split("-"))
        months = max(1, (ey - sy) * 12 + (em - sm) + 1)
        scale  = min(1.0, months / 12)
    else:
        scale = 1.0
    est_chunks = int(total_rows * 4 * scale)
    est_min    = max(1, est_chunks // 6000)
    return html.Span([
        html.I(className="bi bi-info-circle me-1"),
        f"{len(selected)} type(s) · ~",
        html.B(f"{est_chunks:,} chunks"),
        f" · ~{est_min} min to index",
    ])


# 8. Indexing: start on button, poll on interval
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
            return ("Please select at least one meeting type.",
                    current_store, True, False, True)
        if not start or not end:
            return ("Please select a valid date range.",
                    current_store, True, False, True)

        state = pipeline.get_progress()
        if state["status"] == "running":
            return ("\n".join(state["lines"][-12:]),
                    current_store, False, True, True)

        pipeline.start_pipeline(meeting_types, start, end)
        return ("Starting…", current_store, False, True, True)

    elif triggered == "poll-interval":
        state = pipeline.get_progress()
        text  = "\n".join(state["lines"][-12:]) if state["lines"] else "…"

        if state["status"] == "done":
            coverage = pipeline.get_coverage()
            store    = {"collection_name": pipeline.COLLECTION_NAME,
                        "chunks":          coverage["total_chunks"]}
            pipeline.reset()
            return (text, store, True, False, True)

        if state["status"] == "error":
            pipeline.reset()
            return (text, current_store, True, False, True)

        return (text, current_store, False, True, True)

    return dash.no_update, dash.no_update, True, False, dash.no_update


# 9. Run query
@app.callback(
    Output("answer-panel",  "children"),
    Output("sources-panel", "children"),
    Input("ask-button", "n_clicks"),
    State("question-input",   "value"),
    State("filter-start",      "value"),
    State("filter-end",        "value"),
    State("filter-types",      "value"),
    State("filter-content",    "value"),
    State("active-collection", "data"),
    prevent_initial_call=True,
)
def run_query(_, question, f_start, f_end, f_types, f_content, coll_data):
    if not question or not question.strip():
        return (
            dbc.Alert([html.I(className="bi bi-exclamation-circle me-2"),
                       "Enter a question above."],
                      color="warning",
                      className="d-flex align-items-center"),
            "",
        )

    coll_name = (coll_data or {}).get("collection_name")
    if not coll_name:
        return (
            dbc.Alert([html.I(className="bi bi-info-circle me-2"),
                       "No data indexed yet. Use ",
                       html.Strong("Add data"),
                       " at the bottom of the page to get started."],
                      color="info",
                      className="d-flex align-items-center"),
            "",
        )

    chunks = query(
        question,
        n_results=5,
        collection_name=coll_name,
        date_from=f_start or None,
        date_to=f_end or None,
        meeting_types=f_types or None,
        content_type=f_content or None,
    )

    if not chunks:
        return (
            dbc.Alert("No results found for this query. Try broadening your filters.",
                      color="warning"),
            "",
        )

    # Analytics routing
    analytics_ctx, qtype = get_analytics_context(question, _analytics) \
        if _analytics else (None, "rag")

    answer = get_answer(question, chunks, analytics_context=analytics_ctx)

    # Header badges
    active_filters = []
    if f_start or f_end:
        active_filters.append(f"{f_start or '?'} → {f_end or '?'}")
    if f_types:
        active_filters.append(
            f_types[0] if len(f_types) == 1 else f"{len(f_types)} types"
        )
    if f_content:
        active_filters.append(f_content)

    filter_badge = (
        dbc.Badge([html.I(className="bi bi-funnel me-1"),
                   " · ".join(active_filters)],
                  color="secondary", className="ms-2",
                  style={"fontSize": "0.7rem", "fontWeight": "400"})
        if active_filters else None
    )

    source_badge = (
        dbc.Badge("Analytics + RAG", color="info", className="ms-2",
                  style={"fontSize": "0.68rem"})
        if analytics_ctx else
        dbc.Badge("Semantic Search", color="light",
                  text_color="secondary", className="ms-2",
                  style={"fontSize": "0.68rem",
                         "border": f"1px solid {BORDER}"})
    )

    answer_card = dbc.Card([
        dbc.CardHeader([
            html.Div([
                html.I(className="bi bi-robot me-2",
                       style={"color": BLUE}),
                html.Span("Answer",
                          style={"fontWeight": "600", "color": NAVY,
                                 "fontSize": "0.92rem"}),
                source_badge,
                filter_badge,
            ], className="d-flex align-items-center"),
        ], style={"backgroundColor": "#EEF2FF",
                  "borderBottom": f"1px solid {BORDER}",
                  "padding": "9px 16px"}),
        dbc.CardBody([
            dcc.Markdown(answer,
                         style={"fontSize": "0.92rem", "lineHeight": "1.75",
                                "color": "#1E293B"}),
        ], style={"padding": "16px 20px"}),
    ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
              "boxShadow": "0 2px 8px rgba(0,0,0,.07)"})

    if chunks and chunks[0]["score"] > 0.1:
        sources = html.Div([
            html.Div([
                html.I(className="bi bi-journal-text me-2",
                       style={"color": NAVY, "fontSize": "0.85rem"}),
                html.Span("Source Documents",
                          style={"fontWeight": "600", "color": NAVY,
                                 "fontSize": "0.82rem",
                                 "textTransform": "uppercase",
                                 "letterSpacing": "0.05em"}),
            ], className="d-flex align-items-center mb-3"),
            dbc.Row([
                dbc.Col(_source_card(i, c), width=4)
                for i, c in enumerate(chunks[:3])
            ], className="g-3"),
        ])
    else:
        sources = ""

    return answer_card, sources


# ── RUN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nUrbanInfoGPT Dashboard starting...")
    print("Open your browser at: http://127.0.0.1:8050\n")
    app.run(debug=True)
