# src/query.py
#
# PURPOSE: Take a question, find relevant chunks,
# return them with source citations.
# This is the complete RAG retrieval pipeline.

import warnings
warnings.filterwarnings("ignore")

import chromadb
from sentence_transformers import SentenceTransformer

# ── CONFIGURATION ──────────────────────────────────────────
CHROMA_DIR = "data/chroma"
COLLECTION_NAME = "denver_council_2025"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# How many chunks to retrieve per query
N_RESULTS = 5

# ── LOAD ONCE, REUSE MANY TIMES ─────────────────────────────
# We load these outside the function so they're only
# loaded once when the module is imported, not on every query
# Loading a model takes ~2 seconds — we don't want that per query

print("Loading query engine...")
_model = SentenceTransformer(MODEL_NAME)
_client = chromadb.PersistentClient(path=CHROMA_DIR)

try:
    _default_collection = _client.get_collection(COLLECTION_NAME)
    print(f"Ready. {_default_collection.count()} chunks indexed.")
except Exception:
    _default_collection = None
    print(f"Note: default collection '{COLLECTION_NAME}' not found — load data via the dashboard.")


# ── MAIN QUERY FUNCTION ─────────────────────────────────────
def query(question, n_results=N_RESULTS, collection_name=None):
    """
    Takes a natural language question.
    Returns a list of relevant chunks with metadata.

    Pass collection_name to query a dynamically loaded collection;
    omit to use the default collection.
    """

    # Resolve which collection to search
    if collection_name and collection_name != COLLECTION_NAME:
        try:
            col = _client.get_collection(collection_name)
        except Exception:
            col = _default_collection
    else:
        col = _default_collection

    if col is None:
        return []

    # Step 1: Convert question to embedding
    question_embedding = _model.encode(question).tolist()

    # Step 2: Search ChromaDB
    results = col.query(
        query_embeddings=[question_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"]
    )

    # Step 3: Format results
    formatted = []

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for doc, meta, distance in zip(documents, metadatas, distances):

        # Convert distance to similarity score
        # ChromaDB returns distance (lower = more similar)
        # We convert to similarity (higher = more similar)
        similarity = round(1 - distance, 3)

        formatted.append({
            "text": doc,
            "date": meta["date"],
            "meeting": meta["meeting"],
            "page": meta["page"],
            "score": similarity,
        })

    # Sort by score, best first
    formatted.sort(key=lambda x: x["score"], reverse=True)

    return formatted


# ── PRETTY PRINT FOR TESTING ────────────────────────────────
def print_results(question, results):
    print(f"\nQuestion: {question}")
    print("=" * 60)

    for i, r in enumerate(results):
        print(f"\nResult {i+1} | Score: {r['score']} | "
              f"{r['date']} | Page {r['page']}")
        print("-" * 40)
        print(r["text"][:400])

    print("\n" + "=" * 60)


# ── RUN DIRECTLY FOR TESTING ────────────────────────────────
if __name__ == "__main__":

    # Test questions — these demonstrate the system works
    test_questions = [
        "What did council decide about homeless shelter funding?",
        "Were there any votes on affordable housing in 2025?",
        "What contracts were approved for Denver International Airport?",
        "Did council discuss climate or environment issues?",
    ]

    for question in test_questions:
        results = query(question)
        print_results(question, results)
        input("\nPress Enter for next question...")