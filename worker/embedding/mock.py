"""
Mock embedding provider for tests.

Returns deterministic 1536-dim unit vectors based on SHA-256 of the input text.
No external API calls — safe to use in test environments.
"""

import hashlib
import math

EMBEDDING_DIM = 1536


def _text_to_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    extended = digest * (EMBEDDING_DIM // len(digest) + 1)
    raw = [b / 255.0 - 0.5 for b in extended[:EMBEDDING_DIM]]
    norm = math.sqrt(sum(x * x for x in raw))
    if norm > 0:
        raw = [x / norm for x in raw]
    return raw


class MockEmbedder:
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [_text_to_vector(t) for t in texts]
