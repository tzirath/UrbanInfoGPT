# src/dashboard.py
#
# PURPOSE: A web dashboard that lets users query
# Denver City Council minutes through a clean UI.
#
# WHAT YOU'LL LEARN: Dash, callbacks, reactive UI

import warnings
warnings.filterwarnings("ignore")

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dash import Dash, html, dcc, Input, Output, State
import dash_bootstrap_components as dbc
from query import query
from llm import get_answer

# ── APP SETUP ───────────────────────────────────────────────
# Bootstrap gives us nice pre-built CSS components
app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP]
)

app.title = "UrbanInfoGPT"

# ── LAYOUT ──────────────────────────────────────────────────
# This defines what the page looks like.
# Think of it like HTML but written in Python.

app.layout = dbc.Container([

    # Header
    dbc.Row([
        dbc.Col([
            html.H1("UrbanInfoGPT",
                    className="text-center mt-4 mb-1"),
            html.P("Ask questions about Denver City Council meetings (2025)",
                   className="text-center text-muted mb-4"),
        ])
    ]),

    # Search bar + button
    dbc.Row([
        dbc.Col([
            dbc.InputGroup([
                dbc.Input(
                    id="question-input",
                    placeholder="e.g. What did council decide about homeless shelter funding?",
                    type="text",
                    size="lg",
                ),
                dbc.Button(
                    "Ask",
                    id="ask-button",
                    color="primary",
                    size="lg",
                )
            ])
        ], width=10),
    ], justify="center", className="mb-2"),

    # Example questions
    dbc.Row([
        dbc.Col([
            html.P("Try asking:", className="text-muted mb-1 text-center"),
            html.Div([
                dbc.Badge(
                    q, color="secondary",
                    className="me-2 mb-2",
                    style={"cursor": "pointer"},
                    id={"type": "example", "index": i}
                )
                for i, q in enumerate([
                    "What contracts were approved for DIA?",
                    "Did council vote on affordable housing?",
                    "What climate initiatives were discussed?",
                    "How did council vote on the 2026 budget?",
                ])
            ], className="text-center")
        ], width=10)
    ], justify="center", className="mb-4"),

    # Loading spinner + answer area
    dbc.Row([
        dbc.Col([
            dcc.Loading(
                id="loading",
                type="circle",
                children=[
                    # Answer panel
                    html.Div(
                        id="answer-panel",
                        className="mb-4"
                    ),
                    # Sources panel
                    html.Div(
                        id="sources-panel"
                    )
                ]
            )
        ], width=10)
    ], justify="center"),

], fluid=True)


# ── CALLBACKS ───────────────────────────────────────────────
# Callbacks are what make Dash reactive.
# When an input changes → callback fires → output updates.
# This is the core concept of Dash.

@app.callback(
    Output("answer-panel", "children"),
    Output("sources-panel", "children"),
    Input("ask-button", "n_clicks"),
    State("question-input", "value"),
    prevent_initial_call=True
)
def answer_question(n_clicks, question):
    """
    Fires when user clicks Ask.
    Returns updated answer panel and sources panel.
    """

    # No question typed
    if not question or not question.strip():
        return (
            dbc.Alert("Please enter a question.", color="warning"),
            ""
        )

    # Step 1: Retrieve relevant chunks
    chunks = query(question, n_results=5)

    # Step 2: Get Claude's answer
    answer = get_answer(question, chunks)

    # Step 3: Build answer panel
    answer_component = dbc.Card([
        dbc.CardHeader(
            html.H5("Answer", className="mb-0")
        ),
        dbc.CardBody([
            dcc.Markdown(answer)
        ])
    ], className="mb-4")

    # Step 4: Build sources panel
    # Only show sources if we got real results
    if chunks and chunks[0]["score"] > 0.1:
        source_cards = []

        for i, chunk in enumerate(chunks[:3]):  # show top 3
            source_cards.append(
                dbc.Card([
                    dbc.CardHeader(
                        f"Source {i+1} | {chunk['date']} | "
                        f"Page {chunk['page']} | "
                        f"Score: {chunk['score']}"
                    ),
                    dbc.CardBody([
                        html.P(
                            chunk["text"][:300] + "...",
                            className="text-muted small"
                        )
                    ])
                ], className="mb-2")
            )

        sources_component = html.Div([
            html.H5("Source Documents", className="mb-3"),
            html.Div(source_cards)
        ])
    else:
        sources_component = ""

    return answer_component, sources_component


# ── RUN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nUrbanInfoGPT Dashboard starting...")
    print("Open your browser at: http://127.0.0.1:8050\n")
    app.run(debug=True)