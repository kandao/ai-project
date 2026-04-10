import logging

from langdetect import detect

import extractors
import chunking
import embedding
import storage


def detect_language(text: str) -> str:
    """Detect language. Returns 'ja' for Japanese, 'en' for everything else."""
    try:
        lang = detect(text[:2000])
        return "ja" if lang == "ja" else "en"
    except Exception:
        return "en"


async def process(payload: dict, pool) -> None:
    """
    Full ingestion pipeline:
      extract text → detect language → chunk → embed → store → mark ready
    On any failure the document is marked 'failed' and the exception re-raised
    so the Kafka consumer can commit the offset and move on.
    """
    doc_id = payload["doc_id"]
    user_id = payload["user_id"]
    file_path = payload["file_path"]
    file_type = payload["file_type"]

    try:
        text = extractors.extract(file_path, file_type)
        language = detect_language(text)
        chunks = chunking.chunk(text, language)

        if not chunks:
            await storage.update_document_status(doc_id, "ready", pool)
            return

        texts = [c.text for c in chunks]
        embeddings = embedding.embed_batch(texts)
        await storage.store_chunks(doc_id, user_id, chunks, embeddings, language, pool)
        await storage.update_document_status(doc_id, "ready", pool)
        logging.info(
            f"Processed doc {doc_id}: {len(chunks)} chunks, language={language}"
        )
    except Exception as e:
        logging.error(f"Failed to process doc {doc_id}: {e}", exc_info=True)
        await storage.update_document_status(doc_id, "failed", pool)
        raise


async def delete(payload: dict, pool) -> None:
    """Remove all chunks belonging to a document."""
    doc_id = payload["doc_id"]
    await storage.delete_by_doc_id(doc_id, pool)
    logging.info(f"Deleted chunks for doc {doc_id}")
