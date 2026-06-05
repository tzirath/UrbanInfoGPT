import warnings
warnings.filterwarnings("ignore")

import os
from dotenv import load_dotenv
import anthropic

from query import query

load_dotenv()

# COST PER QUERY BREAKDOWN:
# Query refinement (claude-haiku-20240307): ~$0.0003
# 3-4 ChromaDB searches: ~$0.000 (local)
# Resolution auto-lookups (ChromaDB only): ~$0.000
# Main answer (claude-sonnet-4-6): ~$0.010
# ─────────────────────────────────────────
# Total per query: ~$0.010-0.011
# vs previous: ~$0.010
# Improvement: significant quality gain for ~$0.001 extra

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Stores the queries used in the most recent refined_search call.
# Read via get_last_refined_queries() after each search.
_last_refined_queries: list = []


def get_last_refined_queries() -> list:
    return list(_last_refined_queries)


def refine_query(question: str) -> list:
    """
    Takes user's natural language question.
    Returns list of 3 optimized search queries.
    Cost: ~$0.0003 per call
    """
    response = _client.messages.create(
        model="claude-haiku-20240307",
        max_tokens=150,
        system="""You are a search query optimizer for \
Denver City Council meeting minutes. Generate \
precise search queries that will find relevant \
content in official government documents.

Government documents use formal language:
- "approved" not "decided"
- "resolution" not "bill" (usually)
- "council member" not "politician"
- Vendor names and organization names exactly
- Dollar amounts like "$1,176,265"

Return ONLY 3 search queries, one per line.
No numbering, no explanation, no extra text.""",
        messages=[{
            "role": "user",
            "content": (
                f'Original question: "{question}"\n\n'
                "Generate 3 specific search queries to find relevant "
                "Denver City Council meeting minutes. Each query should "
                "target a different angle of the question."
            ),
        }],
    )

    raw = response.content[0].text.strip()
    queries = [q.strip() for q in raw.split("\n") if q.strip()]
    return queries[:3]  # safety: max 3


def refined_search(question: str, n_results: int = 5, **filters) -> list:
    """
    Runs query refinement then executes all searches.
    Combines, deduplicates, and re-ranks results.
    Returns top n_results unique chunks.

    Falls back to direct search if refinement fails.
    """
    global _last_refined_queries

    try:
        refined_queries = refine_query(question)
        print(f"Original: {question}")
        print(f"Refined queries: {refined_queries}")
    except Exception as e:
        print(f"Query refinement failed: {e}, using original")
        refined_queries = [question]

    # Always include the original question as one search
    all_queries = [question] + refined_queries

    # Deduplicate queries (in case refinement echoes the original)
    seen = set()
    unique_queries = []
    for q in all_queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique_queries.append(q)

    _last_refined_queries = list(unique_queries)

    # Run all searches
    all_results = []
    for q in unique_queries:
        try:
            results = query(q, n_results=10, **filters)
            for r in results:
                r["found_by_query"] = q
            all_results.extend(results)
        except Exception as e:
            print(f"Search failed for query '{q}': {e}")
            continue

    if not all_results:
        # Complete fallback
        return query(question, n_results=n_results, **filters)

    # Deduplicate by chunk text, keeping highest score per unique chunk
    seen_texts: set = set()
    unique_results = []
    for r in sorted(all_results, key=lambda x: x["score"], reverse=True):
        text_key = r["text"][:100]
        if text_key not in seen_texts:
            seen_texts.add(text_key)
            unique_results.append(r)

    return unique_results[:n_results]
