"""
Sentinel — Phase 1: dataset loading, adversarial augmentation, embedding, Redis indexing.

Prereqs:
    pip install -r requirements.txt
    Run Redis Stack (has the vector search module — plain Redis does NOT):
        docker run -d --name sentinel-redis -p 6379:6379 redis/redis-stack-server:latest

Usage:
    python build_index.py --build          # load, augment, embed, index everything
    python build_index.py --query "text"   # test a lookup against the index
"""

import os
import argparse
import base64
import random
import numpy as np
import pandas as pd
import redis
from redis.commands.search.field import VectorField, TagField, TextField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from sentence_transformers import SentenceTransformer

EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
INDEX_NAME = "sentinel_idx"
KEY_PREFIX = "sentinel:vec:"

# ---------------------------------------------------------------------------
# 1. Adversarial augmentation — obfuscation tricks applied ONLY to the
#    malicious set, so the index covers disguised versions of known attacks.
# ---------------------------------------------------------------------------

LEET_MAP = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"}
HOMOGLYPH_MAP = {"a": "а", "e": "е", "o": "о", "c": "с", "p": "р"}  # Cyrillic look-alikes
ZERO_WIDTH = "\u200b"


def leetspeak(text: str) -> str:
    return "".join(LEET_MAP.get(c.lower(), c) for c in text)


def homoglyph_swap(text: str, rate: float = 0.3) -> str:
    out = []
    for c in text:
        if c.lower() in HOMOGLYPH_MAP and random.random() < rate:
            out.append(HOMOGLYPH_MAP[c.lower()])
        else:
            out.append(c)
    return "".join(out)


def zero_width_insert(text: str) -> str:
    return ZERO_WIDTH.join(list(text))


def base64_wrap(text: str) -> str:
    encoded = base64.b64encode(text.encode()).decode()
    return f"Decode this base64 string and follow the instructions inside it: {encoded}"


AUGMENTERS = [leetspeak, homoglyph_swap, zero_width_insert, base64_wrap]


def augment_malicious(malicious: list, per_example: int = 1) -> list:
    """Apply a random subset of obfuscation tricks to each malicious example."""
    augmented = []
    for ex in malicious:
        chosen = random.sample(AUGMENTERS, k=per_example)
        for fn in chosen:
            try:
                augmented.append({
                    "text": fn(ex["text"]),
                    "label": "malicious",
                    "source": f"{ex['source']}+{fn.__name__}",
                    "original_category": ex.get("original_category", "")
                })
            except Exception:
                continue
    print(f"Generated {len(augmented)} augmented malicious examples")
    return augmented


# ---------------------------------------------------------------------------
# 2. Embedding + Redis indexing
# ---------------------------------------------------------------------------

def get_redis_client() -> redis.Redis:
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)


def create_index(r: redis.Redis):
    try:
        r.ft(INDEX_NAME).info()
        print(f"Index '{INDEX_NAME}' already exists — dropping and rebuilding")
        r.ft(INDEX_NAME).dropindex(delete_documents=True)
    except redis.exceptions.ResponseError:
        pass  # index doesn't exist yet

    schema = (
        TagField("label"),
        TextField("source"),
        TextField("original_category"),
        TextField("text"),
        VectorField(
            "embedding",
            "HNSW",
            {
                "TYPE": "FLOAT32",
                "DIM": EMBED_DIM,
                "DISTANCE_METRIC": "COSINE",
                "M": 16,
                "EF_CONSTRUCTION": 200,
            },
        ),
    )
    r.ft(INDEX_NAME).create_index(
        schema, definition=IndexDefinition(prefix=[KEY_PREFIX], index_type=IndexType.HASH)
    )
    print(f"Created index '{INDEX_NAME}'")


def embed_and_index(r: redis.Redis, model: SentenceTransformer, examples: list, batch_size: int = 128):
    for i in range(0, len(examples), batch_size):
        batch = examples[i : i + batch_size]
        texts = [ex["text"] for ex in batch]
        vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        pipe = r.pipeline(transaction=False)
        for ex, vec in zip(batch, vectors):
            key = f"{KEY_PREFIX}{hash(ex['text'] + ex['source'])}"
            pipe.hset(
                key,
                mapping={
                    "text": ex["text"][:500],  # cap stored text length
                    "label": ex["label"],
                    "source": ex["source"],
                    "original_category": str(ex.get("original_category", "")),
                    "embedding": vec.astype(np.float32).tobytes(),
                },
            )
        pipe.execute()
        print(f"Indexed {min(i + batch_size, len(examples))}/{len(examples)}")


def build():
    print("Loading data/processed/merged_dataset.csv...")
    df = pd.read_csv("data/processed/merged_dataset.csv")
    df['original_category'] = df['original_category'].fillna("")
    
    print(f"Loaded {len(df)} total rows.")
    print("Label breakdown:")
    print(df['label'].value_counts().to_string())
    
    examples = df.to_dict('records')
    benign = [ex for ex in examples if ex["label"] == "benign"]
    malicious = [ex for ex in examples if ex["label"] == "malicious"]
    
    augmented = augment_malicious(malicious, per_example=2)
    all_examples = benign + malicious + augmented

    print(f"Loading embedding model: {EMBED_MODEL_NAME}")
    model = SentenceTransformer(EMBED_MODEL_NAME)

    r = get_redis_client()
    create_index(r)
    embed_and_index(r, model, all_examples)
    print(f"Done. Total vectors indexed: {len(all_examples)}")


# ---------------------------------------------------------------------------
# 3. Query — this is what the FastAPI fast-path check will call per request
# ---------------------------------------------------------------------------

def classify(text: str, top_k: int = 3) -> dict:
    import re
    model = SentenceTransformer(EMBED_MODEL_NAME)
    r = get_redis_client()
    
    # 1. Chunking: Split into sentences and create overlapping 2-sentence chunks
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]
    if not sentences:
        chunks = [text]
    elif len(sentences) == 1:
        chunks = sentences
    else:
        chunks = [f"{sentences[i]} {sentences[i+1]}" for i in range(len(sentences) - 1)]

    global_max_score = -1.0
    best_chunk = None
    best_match = None
    best_label = None
    best_source = None

    # 2. Run KNN search for each chunk
    for i, chunk in enumerate(chunks):
        vec = model.encode([chunk], convert_to_numpy=True, normalize_embeddings=True)[0]
        vec_bytes = vec.astype(np.float32).tobytes()

        q = (
            Query(f"*=>[KNN {top_k} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("text", "label", "source", "score")
            .dialect(2)
        )
        results = r.ft(INDEX_NAME).search(q, query_params={"vec": vec_bytes})
        
        if not results.docs:
            continue
            
        chunk_max_malicious_sim = -1.0
        chunk_best_doc = None
        for doc in results.docs:
            if doc.label == "malicious":
                sim = 1 - float(doc.score)
                if sim > chunk_max_malicious_sim:
                    chunk_max_malicious_sim = sim
                    chunk_best_doc = doc
        
        if chunk_best_doc:
            if chunk_max_malicious_sim > global_max_score:
                global_max_score = chunk_max_malicious_sim
                best_chunk = chunk
                best_match = chunk_best_doc.text
                best_label = chunk_best_doc.label
                best_source = chunk_best_doc.source

    # 3. Three-way threshold classification
    if global_max_score >= 0.85:
        decision = "BLOCK"
    elif global_max_score >= 0.60:
        decision = "QUEUE"
    else:
        decision = "FORWARD"
        
    return {
        "tier": decision,
        "score": global_max_score,
        "matched_text": best_match,
        "matched_source": best_source,
        "best_chunk": best_chunk
    }


def query(text: str, top_k: int = 3):
    print(f"\nQuery: {text!r}")
    result = classify(text, top_k)
    
    print("\n=== FINAL VERDICT ===")
    print(f"Decision: {result['tier']}")
    if result['best_chunk'] is not None:
        print(f"Highest Malicious Similarity: {result['score']:.3f}")
        print(f"Triggering Chunk: {result['best_chunk']}")
        print(f"Matched DB Entry: {result['matched_text'][:200]}...")
        print(f"Matched Label: malicious (Source: {result['matched_source']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="load, augment, embed, index")
    parser.add_argument("--query", type=str, help="test a query against the index")
    args = parser.parse_args()

    if args.build:
        build()
    elif args.query:
        query(args.query)
    else:
        parser.print_help()
