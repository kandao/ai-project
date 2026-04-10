import cohere


class CohereEmbedder:
    def __init__(self, model: str):
        self.client = cohere.Client()
        self.model = model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Cohere supports up to 96 texts per call."""
        all_embeddings = []
        for i in range(0, len(texts), 96):
            batch = texts[i:i + 96]
            result = self.client.embed(
                texts=batch,
                model=self.model,
                input_type="search_document",
            )
            all_embeddings.extend(result.embeddings)
        return all_embeddings
