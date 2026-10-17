"""Local sentence embeddings (Groq has no embeddings API). The model loads lazily on first use."""

import os
from functools import lru_cache

from dotenv import load_dotenv
from neo4j import Driver

from mnemo.graph.db import database

MAX_CHARS = 1000
BATCH = 256

# Nodes still missing an embedding. Only user messages: they carry the intent and keep the index small.
MISSING = {
    "Topic": "MATCH (n:Topic) WHERE n.embedding IS NULL RETURN elementId(n) AS id, n.name AS text",
    "Message": "MATCH (n:Message {role: 'user'}) WHERE n.embedding IS NULL RETURN elementId(n) AS id, n.text AS text",
}

SET_EMBEDDINGS = """
UNWIND $rows AS row
MATCH (n) WHERE elementId(n) = row.id
CALL db.create.setNodeVectorProperty(n, 'embedding', row.vec)
"""


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer  # slow import; only when needed

    load_dotenv()
    return SentenceTransformer(os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))


def embed(texts: list[str]) -> list[list[float]]:
    vectors = _model().encode([t[:MAX_CHARS] for t in texts], normalize_embeddings=True, batch_size=64)
    return vectors.tolist()


def embed_missing(driver: Driver) -> dict[str, int]:
    """Embed every Topic and user Message that has no embedding yet. Returns counts per label."""
    done = {}
    for label, query in MISSING.items():
        records, _, _ = driver.execute_query(query, database_=database())
        rows = [r.data() for r in records if r["text"]]
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            vecs = embed([r["text"] for r in batch])
            driver.execute_query(SET_EMBEDDINGS, rows=[{"id": r["id"], "vec": v} for r, v in zip(batch, vecs)],
                                 database_=database())
        done[label] = len(rows)
    return done
