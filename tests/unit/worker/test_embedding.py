"""
Unit tests for worker/embedding/

Tests mock provider, determinism, batching, and provider dispatch.
All tests use the mock provider — no external API calls.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../worker"))

import pytest
from unittest.mock import patch, MagicMock

from embedding.mock import MockEmbedder, EMBEDDING_DIM


class TestMockEmbedder:

    def setup_method(self):
        self.embedder = MockEmbedder()

    def test_embed_batch_returns_correct_count(self):
        """4.1: 5 texts → returns 5 vectors."""
        texts = ["text one", "text two", "text three", "text four", "text five"]
        vectors = self.embedder.embed_batch(texts)
        assert len(vectors) == 5

    def test_embed_batch_correct_dimension(self):
        """4.1: Each vector is EMBEDDING_DIM-dimensional."""
        texts = ["hello", "world"]
        vectors = self.embedder.embed_batch(texts)
        for v in vectors:
            assert len(v) == EMBEDDING_DIM

    def test_deterministic_output(self):
        """4.2: Same text twice → identical vectors."""
        embedder = MockEmbedder()
        v1 = embedder.embed_batch(["hello world"])
        v2 = embedder.embed_batch(["hello world"])
        assert v1 == v2

    def test_different_texts_different_vectors(self):
        """4.3: Two distinct texts → vectors differ (not identical)."""
        vectors = self.embedder.embed_batch(["text about dogs", "text about cats"])
        assert vectors[0] != vectors[1]

    def test_large_batch(self):
        """4.4: 200 texts → returns 200 vectors."""
        texts = [f"text number {i}" for i in range(200)]
        vectors = self.embedder.embed_batch(texts)
        assert len(vectors) == 200

    def test_empty_text_no_crash(self):
        """4.5: Empty string input → returns 1 vector without crashing."""
        vectors = self.embedder.embed_batch([""])
        assert len(vectors) == 1
        assert len(vectors[0]) == EMBEDDING_DIM

    def test_unit_normalized(self):
        """Vectors should be unit-normalized (magnitude ≈ 1.0)."""
        import math
        texts = ["normalization test"]
        vectors = self.embedder.embed_batch(texts)
        v = vectors[0]
        magnitude = math.sqrt(sum(x * x for x in v))
        assert abs(magnitude - 1.0) < 1e-6, f"Vector not unit normalized: magnitude={magnitude}"


class TestEmbedderDispatch:

    def test_mock_provider_selected(self):
        """4.6 variant: EMBEDDING_PROVIDER=mock → MockEmbedder selected."""
        import embedding
        embedding._embedder = None  # Reset singleton

        with patch("embedding.settings") as mock_settings:
            mock_settings.EMBEDDING_PROVIDER = "mock"
            mock_settings.EMBEDDING_MODEL = "mock-model"
            embedder = embedding._get_embedder()
        assert isinstance(embedder, MockEmbedder)
        embedding._embedder = None  # Clean up

    def test_unknown_provider_raises(self):
        """Unknown provider raises ValueError."""
        import embedding
        embedding._embedder = None

        with patch("embedding.settings") as mock_settings:
            mock_settings.EMBEDDING_PROVIDER = "totally_unknown"
            mock_settings.EMBEDDING_MODEL = "no-model"
            with pytest.raises(ValueError, match="Unknown EMBEDDING_PROVIDER"):
                embedding._get_embedder()
        embedding._embedder = None

    def test_embed_batch_retries_on_failure(self):
        """4.8: Retry on transient failure — succeeds on second attempt."""
        import embedding
        embedding._embedder = None

        call_count = 0
        original_embed = MockEmbedder.embed_batch

        def flaky_embed(self, texts):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Transient API error")
            return original_embed(self, texts)

        mock_embedder = MockEmbedder()

        with patch.object(MockEmbedder, "embed_batch", flaky_embed):
            with patch("embedding.settings") as mock_settings:
                mock_settings.EMBEDDING_PROVIDER = "mock"
                mock_settings.EMBEDDING_MODEL = "mock"
                embedding._embedder = mock_embedder
                with patch("time.sleep"):  # Don't actually sleep in tests
                    result = embedding.embed_batch(["test text"])

        assert call_count == 2
        assert len(result) == 1
        embedding._embedder = None
