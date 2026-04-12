import time
import logging
from config import settings


_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        provider = settings.EMBEDDING_PROVIDER.lower()
        if provider == "voyage":
            from embedding.voyage import VoyageEmbedder
            _embedder = VoyageEmbedder(settings.EMBEDDING_MODEL)
        elif provider == "cohere":
            from embedding.cohere import CohereEmbedder
            _embedder = CohereEmbedder(settings.EMBEDDING_MODEL)
        elif provider == "openai":
            from embedding.openai import OpenAIEmbedder
            _embedder = OpenAIEmbedder(settings.EMBEDDING_MODEL)
        elif provider == "minimax":
            from embedding.minimax import MiniMaxEmbedder
            _embedder = MiniMaxEmbedder(settings.EMBEDDING_MODEL)
        elif provider == "mock":
            from embedding.mock import MockEmbedder
            _embedder = MockEmbedder()
        else:
            raise ValueError(f"Unknown EMBEDDING_PROVIDER: '{settings.EMBEDDING_PROVIDER}'. "
                             "Supported: openai, voyage, cohere, mock")
    return _embedder


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts with exponential backoff retry (5 attempts).

    Rate-limit errors (RPM/TPM) use a longer fixed delay (30s) to wait out
    the provider's rate-limit window before retrying.
    """
    for attempt in range(5):
        try:
            return _get_embedder().embed_batch(texts)
        except Exception as e:
            if attempt == 4:
                raise
            err_str = str(e).lower()
            if "rate limit" in err_str or "ratelimit" in err_str or "1002" in err_str:
                delay = 30
            else:
                delay = 2 ** attempt
            logging.warning(
                f"Embedding attempt {attempt + 1} failed, retrying in {delay}s: {e}"
            )
            time.sleep(delay)
