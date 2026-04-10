import voyageai


class VoyageEmbedder:
    def __init__(self, model: str):
        self.client = voyageai.Client()
        self.model = model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Voyage supports up to 128 texts per call."""
        all_embeddings = []
        for i in range(0, len(texts), 128):
            batch = texts[i:i + 128]
            result = self.client.embed(batch, model=self.model, input_type="document")
            all_embeddings.extend(result.embeddings)
        return all_embeddings
