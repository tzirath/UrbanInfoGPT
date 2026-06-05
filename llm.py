# src/llm.py
#
# PURPOSE: Take a question + retrieved chunks,
# send them to Claude, get a synthesized answer back.
#


import warnings
warnings.filterwarnings("ignore")

import os
import json
import time
from dotenv import load_dotenv
import anthropic
from utils.links import build_datasette_url

# Load the .env file
load_dotenv()

# ── SET UP CLIENT ───────────────────────────────────────────
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

# ── THE SYSTEM PROMPT ───────────────────────────────────────
# This is the most important part of preventing hallucination.
# We tell Claude exactly what it can and cannot do.

SYSTEM_PROMPT = """You are UrbanInfoGPT, an assistant that answers
questions about Denver City Council meetings and decisions.

Only redirect if the question is clearly unrelated to 
government, such as weather, sports, entertainment, 
or personal questions. Questions about voting patterns, 
council members, legislation, housing, climate policy, 
budgets, and city decisions are ALL within scope. respond with:
"I can only answer questions about Denver City Council meetings
and decisions. Please ask something related to city governance."

You will be given:
1. A question from the user
2. Optionally: pre-computed structured analytics (vote tallies, financial totals)
3. A set of relevant excerpts from actual Denver City Council minutes

Rules:
- When structured analytics data is provided, use it as the primary
  source for quantitative claims (vote counts, spending totals, rankings)
- Use the meeting-minutes excerpts for qualitative context and specifics
- Always mention specific dates, dollar amounts, and resolution numbers
- "When describing resolutions or bills, always explain 
 WHAT the legislation actually does in plain English, 
 not just its procedural status. Include:
 - What the bill/resolution actually changes or approves
 - Who it affects
 - Dollar amounts if present
 - The vote outcome
 Bad: 'Resolution 25-0628 was amended and passed'
 Good: 'Council amended Bill 25-0628, which governs 
        waste hauler recycling standards, requiring 
        education and training programs and reducing 
        the daily bin limit from 350 to 300. The 
        amendment passed 7-6.'"
- Do not speculate or add information not in the provided data
- Do NOT create hyperlinks for street addresses. Only link to source documents using the URLs provided in the excerpt headers.
- When resolution details are provided under 'RESOLUTION DETAILS', use them
  to give specific context about what each resolution involved. Always name
  the resolution AND describe what it was about — never just cite the number.
- Excerpts labeled 'Additional page context' are from the same pages as the
  main results — use them for complete vote tallies, outcomes, and context
  that may not appear in the main excerpts. Always check these for final vote
  outcomes before stating that vote information is unavailable.
- When citing sources, include the URL provided in the excerpt header as a
  markdown link. Format: [View source](URL)
- End your answer with a "Sources" section listing the dates and pages used
"""


# ── ANSWER CACHE ───────────────────────────────────────────
CACHE_DIR       = "data/cache/queries"
CACHE_TTL_HOURS = 1     # bump to 24 for production
CACHE_VERSION   = "v2"  # bump when prompt or schema changes to auto-invalidate


def _cache_key(question: str, filters: dict = None) -> str:
    import hashlib
    content = json.dumps(
        {"q": question.lower().strip(), "f": filters or {}, "v": CACHE_VERSION},
        sort_keys=True,
    )
    return hashlib.md5(content.encode()).hexdigest()


def get_cached_answer(question: str, filters: dict = None):
    path = os.path.join(CACHE_DIR, f"{_cache_key(question, filters)}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            cached = json.load(f)
        if (time.time() - cached["ts"]) / 3600 > CACHE_TTL_HOURS:
            os.remove(path)
            return None
        return cached
    except Exception:
        return None


def save_to_cache(question: str, filters: dict,
                  answer: str, chunks: list, analytics_context) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{_cache_key(question, filters)}.json")
    with open(path, "w") as f:
        json.dump({
            "question":          question,
            "answer":            answer,
            "chunks":            chunks,
            "analytics_context": analytics_context,
            "ts":                time.time(),
        }, f)


# ── FORMAT CHUNKS INTO CONTEXT ──────────────────────────────
def format_context(chunks):
    """
    Takes a list of chunk dictionaries from query.py
    and formats them into a readable context string
    that we'll pass to Claude.
    """
    context_parts = []

    for i, chunk in enumerate(chunks):
        url   = build_datasette_url(chunk["meeting"], chunk["date"], chunk["page"])
        label = "Additional page context" if chunk.get("source") == "page_fetch" \
                else f"Result {i+1}"
        context_parts.append(
            f"[{label} | {chunk['date']} | Page {chunk['page']} | {url}]\n"
            f"{chunk['text']}\n"
        )

    return "\n".join(context_parts)



# ── MAIN FUNCTION ───────────────────────────────────────────
def get_answer(question, chunks, analytics_context=None, filters=None):
    """
    Returns (answer_text, chunks, from_cache).

    Checks the 24-hour disk cache first. On a miss, calls Claude and saves
    the result. `chunks` in the return may differ from the input on a cache
    hit (the stored chunks are returned so the UI stays consistent).
    """
    # Cache check
    cached = get_cached_answer(question, filters)
    if cached:
        return cached["answer"], cached["chunks"], True

    # Guardrail: off-topic question (low similarity, no analytics to fall back on)
    if not analytics_context and chunks[0]["score"] < 0.1:
        return (
            "I couldn't find relevant information about that "
            "in Denver City Council records. Try rephrasing "
            "or ask about a specific council topic.",
            chunks,
            False,
        )

    context = format_context(chunks)

    if analytics_context:
        user_message = f"""STRUCTURED ANALYTICS DATA:
{analytics_context}

SUPPORTING EXCERPTS FROM MEETING MINUTES:
{context}

Based on the analytics data and supporting excerpts, please answer:
{question}"""
    else:
        user_message = f"""Here are relevant excerpts from Denver City Council minutes:

{context}

Based on these excerpts, please answer this question:
{question}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    answer = message.content[0].text
    save_to_cache(question, filters, answer, chunks, analytics_context)
    return answer, chunks, False


# ── TEST IT ─────────────────────────────────────────────────
if __name__ == "__main__":

    # Import query function
    import sys
    sys.path.append("src")
    from query import query

    print("UrbanInfoGPT - LLM Test")
    print("=" * 60)

    test_questions = [
        "What did council decide about homeless shelter funding?",
        "What contracts were approved for Denver International Airport?",
        "Did council discuss climate or environment issues?",
    ]

    for question in test_questions:
        print(f"\nQuestion: {question}")
        print("-" * 40)

        # Step 1: Retrieve relevant chunks
        chunks = query(question, n_results=5)

        # Step 2: Get Claude's answer
        answer = get_answer(question, chunks)

        print(answer)
        print("\n" + "=" * 60)
        input("Press Enter for next question...")