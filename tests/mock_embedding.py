"""
Mock embedding provider for E2E tests.

Returns deterministic 1536-dimensional vectors without calling
any external API (Voyage, Cohere, OpenAI). Each text input gets
a unique-ish but reproducible vector based on a hash of the text.
"""

import hashlib
import math


EMBEDDING_DIM = 1536


def _text_to_vector(text: str) -> list[float]:
    """
    Generate a deterministic unit vector from text content.

    Uses SHA-256 hash bytes to seed the vector values, then normalizes
    to unit length so cosine similarity behaves correctly.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Extend the hash to fill 1536 dimensions by repeating
    extended = digest * (EMBEDDING_DIM // len(digest) + 1)
    raw = [b / 255.0 - 0.5 for b in extended[:EMBEDDING_DIM]]

    # Normalize to unit vector
    norm = math.sqrt(sum(x * x for x in raw))
    if norm > 0:
        raw = [x / norm for x in raw]

    return raw


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Mock replacement for embedding.embed_batch().

    Returns a list of 1536-dim vectors, one per input text.
    """
    return [_text_to_vector(t) for t in texts]


def embed_single(text: str) -> list[float]:
    """Mock replacement for single-text embedding."""
    return _text_to_vector(text)
