"""
MiniMax embedding provider.

MiniMax requires a `type` field ("db" for documents, "query" for queries)
that the standard OpenAI SDK doesn't send, so we call the REST API directly.
"""

import os
import httpx


class MiniMaxEmbedder:
    def __init__(self, model: str = "embo-01"):
        self.model = model
        self.api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")
        base = os.getenv("OPENAI_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
        self.url = f"{base}/embeddings"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using MiniMax REST API (type=db for documents).

        MiniMax format differs from OpenAI:
          request : {"model": ..., "texts": [...], "type": "db"}
          response: {"vectors": [[...], ...], "base_resp": {"status_code": 0, ...}}
        """
        all_embeddings = []
        batch_size = 100
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            payload = {"model": self.model, "texts": batch, "type": "db"}
            resp = httpx.post(self.url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            base = data.get("base_resp", {})
            if base.get("status_code", 0) != 0:
                raise RuntimeError(
                    f"MiniMax embedding error {base.get('status_code')}: {base.get('status_msg')}"
                )
            vectors = data.get("vectors")
            if not vectors:
                raise ValueError(f"MiniMax returned no vectors. Response: {data}")
            all_embeddings.extend(vectors)
        return all_embeddings
