import time
import logging
from config import settings


_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        if settings.EMBEDDING_PROVIDER == "voyage":
            from embedding.voyage import VoyageEmbedder
            _embedder = VoyageEmbedder(settings.EMBEDDING_MODEL)
        else:
            from embedding.cohere import CohereEmbedder
            _embedder = CohereEmbedder(settings.EMBEDDING_MODEL)
    return _embedder


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts with exponential backoff retry (3 attempts)."""
    for attempt in range(3):
        try:
            return _get_embedder().embed_batch(texts)
        except Exception as e:
            if attempt == 2:
                raise
            delay = 2 ** attempt
            logging.warning(
                f"Embedding attempt {attempt + 1} failed, retrying in {delay}s: {e}"
            )
            time.sleep(delay)
