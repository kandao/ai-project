"""
tools/retrieval.py — Hybrid retrieval tool using pgvector + BM25 with RRF fusion.

Uses:
  - pgvector similarity search (vector column on chunks table)
  - BM25 keyword search via pg_search (idx_chunks_bm25 index on content)
  - Reciprocal Rank Fusion (k=60) to merge rankings

Env vars:
    DATABASE_URL        PostgreSQL connection string (fallback)
    EMBEDDING_PROVIDER  "voyage" | "cohere" | "openai"
    EMBEDDING_MODEL     model name override
    VOYAGE_API_KEY / COHERE_API_KEY / OPENAI_API_KEY
"""

import os
from typing import Optional


def _get_embedding(query: str) -> list[float]:
    """Generate query embedding using configured provider."""
    provider = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
    model = os.getenv("EMBEDDING_MODEL")

    if provider == "voyage":
        import voyageai
        client = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        model = model or "voyage-2"
        result = client.embed([query], model=model, input_type="query")
        return result.embeddings[0]

    elif provider == "cohere":
        import cohere
        client = cohere.Client(api_key=os.getenv("COHERE_API_KEY"))
        model = model or "embed-english-v3.0"
        result = client.embed(
            texts=[query],
            model=model,
            input_type="search_query",
            embedding_types=["float"],
        )
        return result.embeddings.float[0]

    else:  # openai (default)
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = model or "text-embedding-3-small"
        result = client.embeddings.create(input=[query], model=model)
        return result.data[0].embedding


def hybrid_retrieval(query: str, top_k: int = 5, db_url: Optional[str] = None) -> str:
    """
    Retrieve top_k chunks using hybrid pgvector + BM25 search with RRF fusion.

    Args:
        query:   Natural language query string.
        top_k:   Number of results to return.
        db_url:  PostgreSQL connection URL. Falls back to DATABASE_URL env var.

    Returns:
        Formatted string of top_k results with content and metadata.
    """
    import psycopg2
    import psycopg2.extras

    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        return "Error: DATABASE_URL not configured."

    try:
        embedding = _get_embedding(query)
    except Exception as e:
        return f"Error generating embedding: {e}"

    # Convert embedding list to pgvector literal
    vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

    # RRF k constant
    k = 60

    sql = f"""
    WITH
    vector_ranked AS (
        SELECT id, content, metadata,
               ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
        FROM chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    ),
    bm25_ranked AS (
        SELECT id, content, metadata,
               ROW_NUMBER() OVER (ORDER BY ts_rank_cd(
                   to_tsvector('english', content),
                   plainto_tsquery('english', %s)
               ) DESC) AS rank
        FROM chunks
        WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        LIMIT %s
    ),
    fused AS (
        SELECT
            COALESCE(v.id, b.id) AS id,
            COALESCE(v.content, b.content) AS content,
            COALESCE(v.metadata, b.metadata) AS metadata,
            COALESCE(1.0 / ({k} + v.rank), 0) +
            COALESCE(1.0 / ({k} + b.rank), 0) AS rrf_score
        FROM vector_ranked v
        FULL OUTER JOIN bm25_ranked b USING (id)
    )
    SELECT id, content, metadata, rrf_score
    FROM fused
    ORDER BY rrf_score DESC
    LIMIT %s
    """

    try:
        conn = psycopg2.connect(url)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (
                vec_literal, vec_literal, top_k * 2,   # vector search params
                query, query, top_k * 2,                # bm25 search params
                top_k,                                  # final limit
            ))
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return f"Database error: {e}"

    if not rows:
        return "No results found."

    parts = []
    for i, row in enumerate(rows, 1):
        meta = row.get("metadata") or {}
        meta_str = ""
        if isinstance(meta, dict):
            meta_str = ", ".join(f"{k}: {v}" for k, v in meta.items() if v)
        elif meta:
            meta_str = str(meta)
        header = f"[{i}] (score: {row['rrf_score']:.4f})"
        if meta_str:
            header += f" — {meta_str}"
        parts.append(f"{header}\n{row['content']}")

    return "\n\n---\n\n".join(parts)
