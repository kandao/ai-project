import asyncpg
from models import Chunk


async def create_pool(database_url: str) -> asyncpg.Pool:
    """
    Create an asyncpg connection pool.
    asyncpg uses the plain postgresql:// scheme — strip the +asyncpg driver
    suffix that SQLAlchemy URLs may include.
    """
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(url)


async def store_chunks(
    doc_id: str,
    user_id: str,
    chunks: list[Chunk],
    embeddings: list[list[float]],
    language: str,
    pool: asyncpg.Pool,
) -> None:
    """
    Batch insert chunks with their embeddings into the chunks table.
    The embedding column is a pgvector type — we cast the Python list to its
    wire representation by converting it to a string and appending ::vector.
    """
    query = """
        INSERT INTO chunks
            (doc_id, user_id, content, metadata, embedding, language, chunk_index)
        VALUES
            ($1::uuid, $2::uuid, $3, $4::jsonb, $5::vector, $6, $7)
    """
    import json

    records = [
        (
            str(doc_id),
            str(user_id),
            chunk.text,
            json.dumps({"chunk_index": chunk.index, "token_count": chunk.token_count}),
            str(embedding),   # e.g. "[0.1, 0.2, ...]" — asyncpg + pgvector accepts this
            language,
            chunk.index,
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    async with pool.acquire() as conn:
        await conn.executemany(query, records)


async def delete_by_doc_id(doc_id: str, pool: asyncpg.Pool) -> None:
    """Delete all chunks for a given document."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM chunks WHERE doc_id = $1::uuid",
            str(doc_id),
        )


async def update_document_status(
    doc_id: str, status: str, pool: asyncpg.Pool
) -> None:
    """Update the status column of a document row."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE documents SET status = $2 WHERE id = $1::uuid",
            str(doc_id),
            status,
        )
