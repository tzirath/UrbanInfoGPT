# src/embed.py
#
# PURPOSE: Read raw meeting data, split into chunks,
# create embeddings, store in ChromaDB.
#

import warnings
warnings.filterwarnings("ignore")

import json
import os
from sentence_transformers import SentenceTransformer
import chromadb

# ── CONFIGURATION ──────────────────────────────────────────

INPUT_FILE = "data/raw/CityCouncil_2025.json"

# ChromaDB will store its files here
CHROMA_DIR = "data/chroma"

# Collection name — like a table name in a regular database
COLLECTION_NAME = "denver_council_2025"

# Chunk settings
CHUNK_SIZE = 512      # max characters per chunk
CHUNK_OVERLAP = 50    # characters shared between adjacent chunks

# The embedding model — runs locally on your CPU, no API needed
# all-MiniLM-L6-v2 is small, fast, and good for semantic search
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


# ── CHUNKING ────────────────────────────────────────────────

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """
    Splits text into overlapping chunks.
    Returns a list of strings.
    """
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Don't cut in the middle of a word
        # Find the last space before the end
        if end < len(text):
            last_space = text.rfind(" ", start, end)
            if last_space > start:
                end = last_space

        chunk = text[start:end].strip()

        # Only keep chunks with real content
        if chunk:
            chunks.append(chunk)

        # Move forward, but overlap with previous chunk
        start = end - overlap

    return chunks


# ── MAIN PIPELINE ───────────────────────────────────────────

def build_vector_store():

    # 1. Load raw data
    print("Loading raw data...")
    with open(INPUT_FILE) as f:
        rows = json.load(f)
    print(f"  Loaded {len(rows)} rows")

    # 2. Load embedding model
    # First run downloads it (~90MB), then it's cached locally
    print(f"\nLoading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    print("  Model loaded")

    # 3. Set up ChromaDB
    print(f"\nSetting up ChromaDB at {CHROMA_DIR}...")
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    # Delete collection if it exists (fresh start)
    try:
        client.delete_collection(COLLECTION_NAME)
        print("  Deleted existing collection")
    except:
        pass

    collection = client.create_collection(COLLECTION_NAME)
    print(f"  Created collection: {COLLECTION_NAME}")

    # 4. Process each row
    print(f"\nProcessing {len(rows)} rows...")

    all_chunks = []
    all_embeddings = []
    all_ids = []
    all_metadata = []

    for i, row in enumerate(rows):

        # Skip rows with no text
        if not row.get("text"):
            continue

        # Split this row's text into chunks
        chunks = chunk_text(row["text"])

        for j, chunk in enumerate(chunks):

            # Create a unique ID using global counter
            # This guarantees uniqueness even if row IDs repeat
            chunk_id = f"chunk_{len(all_chunks)}"

            # Metadata = extra info stored alongside the chunk
            # This is how we'll cite sources later
            metadata = {
                "meeting": row["meeting"],
                "date": row["date"],
                "page": row["page"],
                "source_id": row["id"],
                "chunk_index": j,
            }

            all_chunks.append(chunk)
            all_ids.append(chunk_id)
            all_metadata.append(metadata)

        # Progress update every 100 rows
        if (i + 1) % 100 == 0:
            print(f"  Chunked {i + 1}/{len(rows)} rows "
                  f"({len(all_chunks)} chunks so far)")

    print(f"\nTotal chunks created: {len(all_chunks)}")

    # 5. Create embeddings in batches
    # We batch to avoid running out of memory
    print("\nCreating embeddings (this takes a few minutes)...")
    BATCH_SIZE = 100

    for batch_start in range(0, len(all_chunks), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(all_chunks))
        batch_chunks = all_chunks[batch_start:batch_end]

        # Each chunk becomes a list of 384 numbers
        embeddings = model.encode(batch_chunks).tolist()

        # Store in ChromaDB
        collection.add(
            documents=batch_chunks,
            embeddings=embeddings,
            ids=all_ids[batch_start:batch_end],
            metadatas=all_metadata[batch_start:batch_end],
        )

        print(f"  Embedded and stored chunks "
              f"{batch_start}-{batch_end} "
              f"of {len(all_chunks)}")

    print(f"\nDone! {collection.count()} chunks stored in ChromaDB")
    print(f"ChromaDB saved to: {CHROMA_DIR}")


if __name__ == "__main__":
    build_vector_store()