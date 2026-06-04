import warnings
warnings.filterwarnings("ignore")

import sys
import os
import json
from datetime import date
from calendar import monthrange
from collections import defaultdict, Counter

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import dash
from dash import Dash, html, dcc, Input, Output, State, ALL, ctx
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

import pipeline
from query import query
from llm import get_answer

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
        ("CityCouncil",                           12875),
        ("SpecialMeetingOfTheCityCouncil",            53),
    ],
    "Committees": [
        ("LandUse,TransportationAndInfrastructureCommittee", 1731),
        ("FinanceAndGovernanceCommittee",                    1084),
        ("Safety,Housing,EducationAndHomelessnessCommittee",  939),
        ("Business,Arts,Workforce,ClimateAndAviationServicesCommittee", 574),
        ("TransportationandInfrastructure",                   141),
        ("SafetyAndWell-beingCommittee",                      124),
        ("BudgetandPolicyCommittee",                           94),
        ("FinanceandBusiness",                                 71),
        ("CommunityPlanningandHousing",                        67),
        ("GovernanceandIntergovernmentalRelations",             64),
        ("HealthandSafety",                                    62),
        ("Parks,ArtAndCulture",                                27),
        ("RedistrictingCommittee",                             10),
        ("BudgetHearings",                                      8),
    ],
    "Working Groups": [
        ("EmergencyResponseWorkingGroup",        8),
        ("GeneralObligationBondWorkingGroup",    4),
        ("HousingandHomelessnessWorkingGroup",   4),
        ("PublicSafetyWorkingGroup",             4),
        ("NewcomerResponseWorkingGroup",         2),
    ],
    "Special Issues": [
        ("SouthPlatteRiverCommittee",          89),
        ("SpecialIssuesRedistricting",         34),
        ("SpecialIssuesMarijuana",             32),
        ("SpecialIssuesCityCharter",           10),
        ("SpecialIssues:GreenRoofInitiative",   5),
        ("Mayor-Council",                       4),
        ("PolicyCommittee",                     2),
        ("GeneralPublicCommentSession",         1),
        ("OperationsCityCouncil",               1),
    ],
}

ALL_MEETING_TYPES = [mt for grp in MEETING_GROUPS.values() for mt, _ in grp]
_TYPE_ROWS        = {mt: cnt for grp in MEETING_GROUPS.values() for mt, cnt in grp}

EXAMPLE_QUESTIONS = [
    "What contracts were approved for DIA?",
    "Did council vote on affordable housing?",
    "What climate initiatives were discussed?",
    "How did council vote on the 2026 budget?",
    "What did council decide about homeless shelters?",
    "Were there any major zoning changes in 2025?",
]

# ── DATE RANGE OPTIONS ───────────────────────────────────────
def _month_options():
    opts, d = [], date(2020, 1, 1)
    end = date(2027, 12, 1)
    while d <= end:
        opts.append({"label": d.strftime("%b %Y"), "value": d.strftime("%Y-%m")})
        m, y = d.month + 1, d.year
        if m > 12:
            m, y = 1, y + 1
        d = date(y, m, 1)
    return opts

MONTH_OPTIONS = _month_options()

_today = date(2026, 6, 4)
_sm    = _today.month - 6
_sy    = _today.year
if _sm <= 0:
    _sm += 12; _sy -= 1
DEFAULT_START = f"{_sy}-{_sm:02d}"
DEFAULT_END   = f"{_today.year}-{_today.month:02d}"

# ── STARTUP: load existing collection if available ───────────
_init_coll, _init_chunks = pipeline.get_latest_collection()
_initial_store = (
    {"collection_name": _init_coll, "chunks": _init_chunks,
     "date_from": None, "date_to": None, "n_types": None}
    if _init_coll else None
)

# ── PRECOMPUTED CHARTS (from existing raw JSON) ──────────────
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
        "monthly_mtgs":   {k: len(v) for k, v in sorted(monthly_mtgs.items())},
        "monthly_pages":  dict(sorted(monthly_pages.items())),
        "topic_counts":   dict(sorted(topic_counts.items(), key=lambda x: x[1])),
    }

_CHART_DATA = _load_chart_data()

_MONTH_ABBR = {
    "2025-01": "Jan", "2025-02": "Feb", "2025-03": "Mar",
    "2025-04": "Apr", "2025-05": "May", "2025-06": "Jun",
    "2025-07": "Jul", "2025-08": "Aug", "2025-09": "Sep",
    "2025-10": "Oct", "2025-11": "Nov", "2025-12": "Dec",
}

_CHART_LAYOUT = dict(
    paper_bgcolor="white", plot_bgcolor="white",
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#374151"),
    margin=dict(l=0, r=32, t=40, b=0),
)


def _topic_fig():
    if not _CHART_DATA:
        return go.Figure()
    t = list(_CHART_DATA["topic_counts"].keys())
    c = list(_CHART_DATA["topic_counts"].values())
    fig = go.Figure(go.Bar(
        x=c, y=t, orientation="h",
        marker=dict(color=c, colorscale=[[0, "#7CB9E8"], [1, NAVY]]),
        text=c, textposition="outside", textfont=dict(size=11),
        hovertemplate="%{y}: %{x} pages<extra></extra>",
    ))
    fig.update_layout(
        **_CHART_LAYOUT, height=340,
        title=dict(text="Discussion Topics — 2025 City Council",
                   font=dict(size=13, color=NAVY), x=0),
        xaxis=dict(title="Pages mentioning topic", showgrid=True,
                   gridcolor="#F1F5F9", showline=False, zeroline=False),
        yaxis=dict(showgrid=False, tickfont=dict(size=11)),
    )
    return fig


def _activity_fig():
    if not _CHART_DATA:
        return go.Figure()
    months   = [_MONTH_ABBR[m] for m in _CHART_DATA["monthly_mtgs"]]
    meetings = list(_CHART_DATA["monthly_mtgs"].values())
    pages    = [_CHART_DATA["monthly_pages"][m] for m in _CHART_DATA["monthly_mtgs"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=months, y=pages, name="Pages", marker_color="#7CB9E8",
        hovertemplate="%{x}: %{y} pages<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=months, y=meetings, name="Meetings", yaxis="y2",
        mode="lines+markers",
        line=dict(color=NAVY, width=2), marker=dict(size=6, color=NAVY),
        hovertemplate="%{x}: %{y} meetings<extra></extra>",
    ))
    fig.update_layout(
        **_CHART_LAYOUT, height=340,
        title=dict(text="Monthly Activity — 2025",
                   font=dict(size=13, color=NAVY), x=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=11)),
        xaxis=dict(showgrid=False, showline=False),
        yaxis=dict(title="Pages", showgrid=True, gridcolor="#F1F5F9",
                   zeroline=False, showline=False),
        yaxis2=dict(title="Meetings", overlaying="y", side="right",
                    showgrid=False, zeroline=False, dtick=1, showline=False),
        bargap=0.25,
    )
    return fig


# ── UI COMPONENTS ────────────────────────────────────────────
def _kpi(value_id, label, icon):
    return dbc.Card([
        dbc.CardBody([
            html.I(className=f"bi bi-{icon}",
                   style={"fontSize": "1.3rem", "color": BLUE, "marginBottom": "5px"}),
            html.Div(id=value_id,
                     style={"fontSize": "1.7rem", "fontWeight": "700",
                            "color": NAVY, "lineHeight": "1"}),
            html.Div(label, style={"fontSize": "0.76rem", "color": SLATE,
                                   "marginTop": "4px", "fontWeight": "500",
                                   "letterSpacing": "0.04em",
                                   "textTransform": "uppercase"}),
        ], className="d-flex flex-column align-items-center text-center",
           style={"padding": "18px 10px"}),
    ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
              "boxShadow": "0 1px 4px rgba(0,0,0,.06)"})


def _group_checklist(group_id, group_name, types):
    return html.Div([
        html.Div(group_name,
                 style={"fontSize": "0.72rem", "fontWeight": "700",
                        "color": NAVY, "textTransform": "uppercase",
                        "letterSpacing": "0.07em", "marginBottom": "6px",
                        "paddingBottom": "4px", "borderBottom": f"2px solid {NAVY}"}),
        dbc.Checklist(
            id=group_id,
            options=[
                {"label": html.Span([
                    html.Span(mt, style={"fontSize": "0.8rem"}),
                    html.Span(f" ({cnt:,})",
                              style={"fontSize": "0.72rem", "color": SLATE}),
                ]), "value": mt}
                for mt, cnt in types
            ],
            value=["CityCouncil"] if group_id == "types-fullcouncil" else [],
            input_style={"cursor": "pointer"},
            label_style={"marginBottom": "4px", "cursor": "pointer", "lineHeight": "1.4"},
        ),
    ])


def _source_card(i, chunk):
    pct   = int(chunk["score"] * 100)
    color = "success" if pct >= 70 else ("warning" if pct >= 50 else "secondary")
    return dbc.Card([
        dbc.CardBody([
            html.Div([
                dbc.Badge(f"Source {i+1}", color="primary",
                          className="me-2", style={"fontSize": "0.7rem"}),
                dbc.Badge(f"{pct}% match", color=color,
                          style={"fontSize": "0.7rem"}),
            ], className="mb-2"),
            html.Div([
                html.Span(chunk["date"],
                          style={"fontSize": "0.78rem", "fontWeight": "600",
                                 "color": NAVY, "marginRight": "8px"}),
                html.Span(f"Page {chunk['page']}",
                          style={"fontSize": "0.78rem", "color": SLATE}),
            ], className="mb-2"),
            html.P(chunk["text"][:260] + "…",
                   style={"fontSize": "0.82rem", "color": "#4B5563",
                          "marginBottom": 0, "lineHeight": "1.5"}),
        ], style={"padding": "14px 16px"}),
    ], style={"border": f"1px solid {BORDER}", "borderRadius": "8px",
              "boxShadow": "0 1px 3px rgba(0,0,0,.05)", "height": "100%"})


# ── APP ──────────────────────────────────────────────────────
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.FLATLY,
        dbc.icons.BOOTSTRAP,
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    ],
)
app.title = "UrbanInfoGPT — Denver City Council Intelligence"


# ── LAYOUT ───────────────────────────────────────────────────
app.layout = html.Div([

    # ── Topbar ──────────────────────────────────────────────
    html.Div([
        dbc.Container([
            dbc.Row([
                dbc.Col([
                    html.H1("UrbanInfoGPT",
                            style={"color": "white", "fontWeight": "700",
                                   "fontSize": "1.7rem", "letterSpacing": "-0.4px",
                                   "marginBottom": "2px"}),
                    html.P("AI-powered search across Denver City Council minutes",
                           style={"color": "#94A3B8", "marginBottom": 0,
                                  "fontSize": "0.88rem"}),
                ], className="d-flex flex-column justify-content-center"),
                dbc.Col([
                    html.Div([
                        dbc.Badge([html.I(className="bi bi-building me-1"),
                                   "Denver City Council"],
                                  color="primary", className="me-2 px-3 py-2"),
                        dbc.Badge([html.I(className="bi bi-cpu me-1"),
                                   "Claude + RAG"],
                                  color="secondary", className="px-3 py-2"),
                    ], className="d-flex justify-content-end align-items-center"),
                ], className="d-flex align-items-center"),
            ], className="g-0", style={"minHeight": "72px"}),
        ], fluid=True, style={"padding": "0 24px"}),
    ], style={"backgroundColor": NAVY, "borderBottom": f"3px solid {NAVY_DARK}"}),

    # ── Main ────────────────────────────────────────────────
    dbc.Container([

        # ── Data Configuration Card ──────────────────────
        dbc.Card([
            dbc.CardHeader([
                html.Div([
                    html.Div([
                        html.I(className="bi bi-database-fill-gear me-2",
                               style={"color": BLUE}),
                        html.Span("Data Source",
                                  style={"fontWeight": "600", "color": NAVY,
                                         "fontSize": "0.95rem"}),
                    ], className="d-flex align-items-center"),
                    html.Div(id="active-badge"),
                ], className="d-flex justify-content-between align-items-center"),
            ], style={"backgroundColor": "#EEF2FF", "padding": "10px 16px",
                      "borderBottom": f"1px solid {BORDER}"}),

            dbc.CardBody([
                # Row 1: Date range + action buttons
                dbc.Row([
                    dbc.Col([
                        html.Label("Start", style={"fontSize": "0.78rem",
                                                   "fontWeight": "600",
                                                   "color": SLATE,
                                                   "marginBottom": "4px"}),
                        dcc.Dropdown(
                            id="start-date",
                            options=MONTH_OPTIONS,
                            value=DEFAULT_START,
                            clearable=False,
                            style={"fontSize": "0.85rem"},
                        ),
                    ], width=3),
                    dbc.Col([
                        html.Label("End", style={"fontSize": "0.78rem",
                                                 "fontWeight": "600",
                                                 "color": SLATE,
                                                 "marginBottom": "4px"}),
                        dcc.Dropdown(
                            id="end-date",
                            options=MONTH_OPTIONS,
                            value=DEFAULT_END,
                            clearable=False,
                            style={"fontSize": "0.85rem"},
                        ),
                    ], width=3),
                    dbc.Col([
                        html.Div([
                            dbc.Button([
                                html.I(className="bi bi-clock-history me-1"),
                                "Last 6 months",
                            ], id="btn-preset-6m", color="light", size="sm",
                               style={"fontSize": "0.8rem", "border": f"1px solid {BORDER}",
                                      "fontWeight": "500"}),
                            dbc.Button([
                                html.I(className="bi bi-play-fill me-1"),
                                "Load Data",
                            ], id="load-btn", color="primary", size="sm",
                               style={"fontSize": "0.82rem", "fontWeight": "600",
                                      "paddingLeft": "18px", "paddingRight": "18px"}),
                        ], className="d-flex gap-2 align-items-end",
                           style={"paddingTop": "22px"}),
                    ], width=6),
                ], className="mb-4 g-3"),

                # Row 2: Meeting type selector
                html.Div([
                    html.Div([
                        html.Span("Meeting Types",
                                  style={"fontWeight": "600", "color": NAVY,
                                         "fontSize": "0.88rem"}),
                        html.Div([
                            dbc.Button("Select All", id="btn-select-all",
                                       color="light", size="sm",
                                       style={"fontSize": "0.75rem",
                                              "border": f"1px solid {BORDER}"}),
                            dbc.Button("Clear All", id="btn-clear-all",
                                       color="light", size="sm",
                                       style={"fontSize": "0.75rem",
                                              "border": f"1px solid {BORDER}"}),
                        ], className="d-flex gap-2"),
                    ], className="d-flex justify-content-between align-items-center mb-3"),

                    dbc.Row([
                        dbc.Col([
                            _group_checklist(
                                "types-fullcouncil", "Full Council",
                                MEETING_GROUPS["Full Council"],
                            ),
                        ], width=3),
                        dbc.Col([
                            _group_checklist(
                                "types-committees", "Committees",
                                MEETING_GROUPS["Committees"],
                            ),
                        ], width=3,
                           style={"maxHeight": "220px", "overflowY": "auto",
                                  "paddingRight": "8px"}),
                        dbc.Col([
                            _group_checklist(
                                "types-workinggroups", "Working Groups",
                                MEETING_GROUPS["Working Groups"],
                            ),
                        ], width=3),
                        dbc.Col([
                            _group_checklist(
                                "types-special", "Special Issues",
                                MEETING_GROUPS["Special Issues"],
                            ),
                        ], width=3,
                           style={"maxHeight": "220px", "overflowY": "auto",
                                  "paddingRight": "8px"}),
                    ], className="g-3"),
                ], style={"backgroundColor": "#F8FAFC", "borderRadius": "8px",
                          "padding": "14px 16px",
                          "border": f"1px solid {BORDER}"}),

                # Estimate row
                html.Div(id="estimate-text",
                         style={"marginTop": "12px", "fontSize": "0.82rem",
                                "color": SLATE}),

                # Progress collapse
                dbc.Collapse([
                    html.Hr(style={"borderColor": BORDER, "margin": "14px 0"}),
                    html.Pre(
                        id="progress-text",
                        style={
                            "backgroundColor": NAVY_DARK,
                            "color": "#E2E8F0",
                            "borderRadius": "6px",
                            "padding": "12px 16px",
                            "fontSize": "0.8rem",
                            "lineHeight": "1.6",
                            "height": "160px",
                            "overflowY": "auto",
                            "fontFamily": "ui-monospace, 'Fira Code', monospace",
                            "marginBottom": 0,
                            "whiteSpace": "pre-wrap",
                        },
                    ),
                ], id="progress-collapse", is_open=False),

            ], style={"padding": "16px 20px"}),
        ], className="mt-4 mb-4",
           style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
                  "boxShadow": "0 2px 8px rgba(0,0,0,.08)"}),

        # ── KPI Row ──────────────────────────────────────
        dbc.Row([
            dbc.Col(_kpi("kpi-chunks",  "Chunks Indexed",  "files"),          width=3),
            dbc.Col(_kpi("kpi-range",   "Date Range",      "calendar3-range"), width=3),
            dbc.Col(_kpi("kpi-types",   "Meeting Types",   "diagram-3"),       width=3),
            dbc.Col(_kpi("kpi-source",  "Active Dataset",  "database"),        width=3),
        ], className="mb-4 g-3"),

        # ── Charts Row ───────────────────────────────────
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        dcc.Graph(figure=_topic_fig(),
                                  config={"displayModeBar": False},
                                  style={"height": "340px"}),
                    ], style={"padding": "16px 20px"}),
                ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
                          "boxShadow": "0 1px 4px rgba(0,0,0,.06)"}),
            ], width=7),
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        dcc.Graph(figure=_activity_fig(),
                                  config={"displayModeBar": False},
                                  style={"height": "340px"}),
                    ], style={"padding": "16px 20px"}),
                ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
                          "boxShadow": "0 1px 4px rgba(0,0,0,.06)"}),
            ], width=5),
        ], className="mb-4 g-3"),

        # ── Query divider ─────────────────────────────────
        html.Div([
            html.Hr(style={"borderColor": BORDER, "margin": "0"}),
            html.Span("Query the Record",
                      style={"display": "block", "backgroundColor": BG,
                             "width": "fit-content",
                             "margin": "-12px auto 0",
                             "padding": "0 16px",
                             "color": NAVY, "fontWeight": "600",
                             "fontSize": "0.85rem", "letterSpacing": "0.06em",
                             "textTransform": "uppercase"}),
        ], className="mb-4"),

        # ── Search row ───────────────────────────────────
        dbc.Row([
            dbc.Col([
                dbc.InputGroup([
                    html.Span([html.I(className="bi bi-search")],
                              className="input-group-text",
                              style={"backgroundColor": "white",
                                     "borderRight": "none", "color": SLATE}),
                    dbc.Input(
                        id="question-input",
                        placeholder="e.g. What did council decide about homeless shelter funding?",
                        type="text", size="lg",
                        style={"borderLeft": "none", "boxShadow": "none",
                               "fontSize": "0.95rem"},
                    ),
                    dbc.Button([html.I(className="bi bi-send me-2"), "Ask"],
                               id="ask-button", color="primary", size="lg",
                               style={"fontWeight": "600",
                                      "paddingLeft": "24px", "paddingRight": "24px"}),
                ], style={"boxShadow": "0 2px 8px rgba(0,0,0,.10)",
                          "borderRadius": "8px", "overflow": "hidden"}),
            ], width={"size": 10, "offset": 1}),
        ], className="mb-3"),

        # ── Example chips ────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.Div([
                    html.Span("Try: ", style={"fontSize": "0.82rem", "color": SLATE,
                                              "fontWeight": "500", "marginRight": "6px"}),
                    *[
                        dbc.Badge(
                            q, color="light", text_color="primary",
                            className="me-2 mb-1 px-3 py-2",
                            style={"cursor": "pointer", "fontWeight": "500",
                                   "fontSize": "0.78rem",
                                   "border": f"1px solid {BORDER}",
                                   "borderRadius": "20px"},
                            id={"type": "example", "index": i},
                        )
                        for i, q in enumerate(EXAMPLE_QUESTIONS)
                    ],
                ], className="d-flex flex-wrap align-items-center"),
            ], width={"size": 10, "offset": 1}),
        ], className="mb-4"),

        # ── Results ──────────────────────────────────────
        dbc.Row([
            dbc.Col([
                dcc.Loading(id="loading", type="dot", color=BLUE, children=[
                    html.Div(id="answer-panel", className="mb-3"),
                    html.Div(id="sources-panel"),
                ]),
            ], width={"size": 10, "offset": 1}),
        ], className="mb-5"),

    ], fluid=True,
       style={"backgroundColor": BG, "minHeight": "calc(100vh - 75px)",
              "padding": "0 24px"}),

    # ── Footer ──────────────────────────────────────────────
    html.Div([
        dbc.Container([
            html.P([
                "UrbanInfoGPT · Answers sourced from official ",
                html.A("Denver City Council minutes",
                       href="https://denver.gov/city-council", target="_blank",
                       style={"color": "#94A3B8"}),
                " · Powered by Claude + RAG",
            ], style={"color": "#64748B", "fontSize": "0.78rem",
                      "marginBottom": 0, "textAlign": "center"}),
        ], fluid=True),
    ], style={"backgroundColor": NAVY_DARK, "padding": "16px 0",
              "borderTop": "1px solid #0f2035"}),

    # ── Hidden state ────────────────────────────────────────
    dcc.Store(id="active-collection", data=_initial_store),
    dcc.Interval(id="poll-interval", interval=1000, n_intervals=0, disabled=True),

], style={"fontFamily": "Inter, system-ui, sans-serif", "backgroundColor": BG})


# ── CALLBACKS ────────────────────────────────────────────────

# 1. Date range preset
@app.callback(
    Output("start-date", "value"),
    Output("end-date",   "value"),
    Input("btn-preset-6m", "n_clicks"),
    prevent_initial_call=True,
)
def apply_date_preset(_):
    return DEFAULT_START, DEFAULT_END


# 2. Meeting type presets
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
def apply_type_preset(_, _c, _p):
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


# 3. Estimated index size
@app.callback(
    Output("estimate-text", "children"),
    Input("types-fullcouncil",   "value"),
    Input("types-committees",    "value"),
    Input("types-workinggroups", "value"),
    Input("types-special",       "value"),
    Input("start-date", "value"),
    Input("end-date",   "value"),
)
def update_estimate(fc, co, wg, sp, start, end):
    selected = (fc or []) + (co or []) + (wg or []) + (sp or [])
    if not selected:
        return html.Span("No meeting types selected.", style={"color": "#EF4444"})

    total_rows = sum(_TYPE_ROWS.get(mt, 0) for mt in selected)
    est_chunks = total_rows * 4
    est_min    = max(1, est_chunks // 6000)

    # Scale by proportion of year selected
    if start and end:
        sy, sm = map(int, start.split("-"))
        ey, em = map(int, end.split("-"))
        months = max(1, (ey - sy) * 12 + (em - sm) + 1)
        scale  = min(1.0, months / 12)
        est_chunks = int(est_chunks * scale)
        est_min    = max(1, est_chunks // 6000)

    return html.Span([
        html.I(className="bi bi-info-circle me-1"),
        f"{len(selected)} type(s) selected · ",
        html.B(f"~{est_chunks:,} chunks"),
        f" · ~{est_min} min to index",
        html.Span(" (approximate, based on full history scaled to date range)",
                  style={"color": "#9CA3AF"}),
    ])


# 4. Pipeline: start on button click, poll on interval
@app.callback(
    Output("progress-text",      "children"),
    Output("active-collection",  "data"),
    Output("poll-interval",      "disabled"),
    Output("load-btn",           "disabled"),
    Output("progress-collapse",  "is_open"),
    Input("load-btn",      "n_clicks"),
    Input("poll-interval", "n_intervals"),
    State("start-date",           "value"),
    State("end-date",             "value"),
    State("types-fullcouncil",    "value"),
    State("types-committees",     "value"),
    State("types-workinggroups",  "value"),
    State("types-special",        "value"),
    State("active-collection",    "data"),
    prevent_initial_call=True,
)
def handle_pipeline(load_click, n_intervals,
                    start, end, fc, co, wg, sp, current_coll):
    triggered = ctx.triggered_id

    if triggered == "load-btn":
        meeting_types = (fc or []) + (co or []) + (wg or []) + (sp or [])
        if not meeting_types:
            return ("Please select at least one meeting type.",
                    current_coll, True, False, True)

        if not start or not end:
            return ("Please select a valid date range.",
                    current_coll, True, False, True)

        # Check if already indexed (cache hit)
        coll_name = pipeline.collection_name_for(meeting_types, start, end)
        count = pipeline.collection_exists(coll_name)
        if count:
            store = {"collection_name": coll_name, "chunks": count,
                     "date_from": start, "date_to": end,
                     "n_types": len(meeting_types)}
            return (
                f"✓ Loaded from cache: {count:,} chunks already indexed.",
                store, True, False, True,
            )

        # Check for already-running pipeline
        state = pipeline.get_progress()
        if state["status"] == "running":
            return (
                "\n".join(state["lines"][-12:]),
                current_coll, False, True, True,
            )

        # Start pipeline
        pipeline.start_pipeline(meeting_types, start, end)
        return ("Starting pipeline…", current_coll, False, True, True)

    elif triggered == "poll-interval":
        state = pipeline.get_progress()
        text  = "\n".join(state["lines"][-12:]) if state["lines"] else "…"

        if state["status"] == "done":
            coll  = state["collection_name"]
            count = pipeline.collection_exists(coll)
            n_types = len((fc or []) + (co or []) + (wg or []) + (sp or []))
            store = {"collection_name": coll, "chunks": count,
                     "date_from": start, "date_to": end,
                     "n_types": n_types or None}
            pipeline.reset()
            return (text, store, True, False, True)

        if state["status"] == "error":
            pipeline.reset()
            return (text, current_coll, True, False, True)

        # Still running
        return (text, current_coll, False, True, True)

    return dash.no_update, dash.no_update, True, False, dash.no_update


# 5. KPIs + active badge from store
@app.callback(
    Output("kpi-chunks",  "children"),
    Output("kpi-range",   "children"),
    Output("kpi-types",   "children"),
    Output("kpi-source",  "children"),
    Output("active-badge", "children"),
    Input("active-collection", "data"),
)
def update_kpis(coll_data):
    if not coll_data or not coll_data.get("collection_name"):
        badge = dbc.Badge([html.I(className="bi bi-exclamation-circle me-1"),
                           "No data loaded"],
                          color="warning", className="px-3 py-2",
                          style={"fontSize": "0.75rem"})
        return "—", "—", "—", "—", badge

    name   = coll_data["collection_name"]
    chunks = coll_data.get("chunks") or pipeline.collection_exists(name)

    # Parse range from collection name if available
    d_from = coll_data.get("date_from") or ""
    d_to   = coll_data.get("date_to")   or ""
    if not d_from and name.count("_") >= 3:
        parts  = name.split("_")
        d_from = parts[-2] if len(parts) >= 2 else ""
        d_to   = parts[-1] if len(parts) >= 1 else ""

    rng    = f"{d_from} → {d_to}" if d_from and d_to else "—"
    n_types = coll_data.get("n_types") or "—"

    short_name = name[:22] + "…" if len(name) > 25 else name

    badge = dbc.Badge([html.I(className="bi bi-check-circle-fill me-1"),
                       f"{chunks:,} chunks ready"],
                      color="success", className="px-3 py-2",
                      style={"fontSize": "0.75rem"})

    return f"{chunks:,}", rng, str(n_types), short_name, badge


# 6. Fill question from example chip
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


# 7. Query + answer
@app.callback(
    Output("answer-panel",  "children"),
    Output("sources-panel", "children"),
    Input("ask-button", "n_clicks"),
    State("question-input",   "value"),
    State("active-collection", "data"),
    prevent_initial_call=True,
)
def answer_question(_, question, coll_data):
    if not question or not question.strip():
        return (
            dbc.Alert([html.I(className="bi bi-exclamation-circle me-2"),
                       "Please enter a question before clicking Ask."],
                      color="warning", className="d-flex align-items-center"),
            "",
        )

    coll_name = (coll_data or {}).get("collection_name")
    if not coll_name:
        return (
            dbc.Alert([html.I(className="bi bi-info-circle me-2"),
                       "No dataset loaded. Use the Data Source panel above to index a date range first."],
                      color="info", className="d-flex align-items-center"),
            "",
        )

    chunks = query(question, n_results=5, collection_name=coll_name)

    if not chunks:
        return (
            dbc.Alert("No indexed data found. Please load a dataset first.",
                      color="warning"),
            "",
        )

    answer = get_answer(question, chunks)

    answer_card = dbc.Card([
        dbc.CardHeader([
            html.Div([
                html.I(className="bi bi-robot me-2",
                       style={"color": BLUE, "fontSize": "1rem"}),
                html.Span("Answer", style={"fontWeight": "600", "color": NAVY,
                                           "fontSize": "0.95rem"}),
            ], className="d-flex align-items-center"),
        ], style={"backgroundColor": "#EEF2FF",
                  "borderBottom": f"1px solid {BORDER}",
                  "padding": "10px 16px"}),
        dbc.CardBody([
            dcc.Markdown(answer, style={"fontSize": "0.92rem", "lineHeight": "1.7",
                                        "color": "#1E293B"}),
        ], style={"padding": "16px 20px"}),
    ], style={"border": f"1px solid {BORDER}", "borderRadius": "10px",
              "boxShadow": "0 2px 8px rgba(0,0,0,.07)"})

    if chunks and chunks[0]["score"] > 0.1:
        sources = html.Div([
            html.Div([
                html.I(className="bi bi-journal-text me-2",
                       style={"color": NAVY, "fontSize": "0.9rem"}),
                html.Span("Source Documents",
                          style={"fontWeight": "600", "color": NAVY,
                                 "fontSize": "0.88rem", "textTransform": "uppercase",
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
