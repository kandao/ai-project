"""
Unit test conftest for worker tests.

Sets up environment variables and mocks required to import worker modules
without a running infrastructure (no Kafka, no real DB, no MeCab).
"""

import os
import sys
from unittest.mock import MagicMock

# ── Required env vars for worker config (pydantic-settings validates at import) ──
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("DATABASE_URL", "postgresql://docqa:docqa@localhost/docqa_test")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")


class _MockWord:
    def __init__(self, surface: str):
        self.surface = surface


class _MockTagger:
    """Minimal tagger that splits Japanese text into individual characters."""
    def __call__(self, text: str):
        return [_MockWord(ch) for ch in text if ch.strip()]


def _maybe_mock_fugashi():
    """
    If fugashi.Tagger() fails (MeCab not installed), inject a mock
    so chunking/japanese.py can be imported without a real MeCab installation.
    """
    try:
        import fugashi
        fugashi.Tagger()  # Test if MeCab is available
        return  # Real MeCab works — no mocking needed
    except Exception:
        pass

    mock_fugashi = MagicMock()
    mock_fugashi.Tagger = _MockTagger
    sys.modules["fugashi"] = mock_fugashi


# Must run before any test module is collected
_maybe_mock_fugashi()
