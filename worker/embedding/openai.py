import os
import openai


class OpenAIEmbedder:
    def __init__(self, model: str = "text-embedding-3-small"):
        # Always pass base_url explicitly so the SDK never reads OPENAI_BASE_URL
        # from the environment (an empty string env var causes a protocol error).
        base_url = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        self.client = openai.OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=base_url,
        )
        self.model = model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. OpenAI supports up to 2048 inputs per call."""
        all_embeddings = []
        # Stay well under the 2048-input limit
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            result = self.client.embeddings.create(input=batch, model=self.model)
            # Results are ordered by index
            result.data.sort(key=lambda x: x.index)
            all_embeddings.extend([item.embedding for item in result.data])
        return all_embeddings
