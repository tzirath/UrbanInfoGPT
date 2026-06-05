# src/llm.py
#
# PURPOSE: Take a question + retrieved chunks,
# send them to Claude, get a synthesized answer back.
#
# WHAT YOU'LL LEARN: API calls, prompt engineering,
# how to prevent hallucination through RAG prompting

import warnings
warnings.filterwarnings("ignore")

import os
from dotenv import load_dotenv
import anthropic

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

If the question is not related to Denver City Council meetings,
policies, votes, contracts, or city governance, respond with:
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
- Keep answers concise and factual
- Do not speculate or add information not in the provided data
- When resolution details are provided under 'RESOLUTION DETAILS', use them
  to give specific context about what each resolution involved. Always name
  the resolution AND describe what it was about — never just cite the number.
- End your answer with a "Sources" section listing the dates and pages used
"""


# ── FORMAT CHUNKS INTO CONTEXT ──────────────────────────────
def format_context(chunks):
    """
    Takes a list of chunk dictionaries from query.py
    and formats them into a readable context string
    that we'll pass to Claude.
    """
    context_parts = []

    for i, chunk in enumerate(chunks):
        context_parts.append(
            f"[Excerpt {i+1} | {chunk['date']} | Page {chunk['page']}]\n"
            f"{chunk['text']}\n"
        )

    return "\n".join(context_parts)



# ── MAIN FUNCTION ───────────────────────────────────────────
def get_answer(question, chunks, analytics_context=None):
    """
    Takes a question, retrieved chunks, and optional pre-computed analytics.
    Returns Claude's synthesized answer as a string.

    analytics_context: formatted string from analytics.query_router,
                       or None for pure RAG answers.
    """
    # Guardrail: off-topic question (low similarity, no analytics to fall back on)
    if not analytics_context and chunks[0]["score"] < 0.1:
        return ("I couldn't find relevant information about that "
                "in Denver City Council records. Try rephrasing "
                "or ask about a specific council topic.")

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

    # Call Claude API
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message}
        ]
    )

    # Extract the text response
    return message.content[0].text


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