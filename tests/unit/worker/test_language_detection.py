"""
Unit tests for worker/pipeline.py:detect_language()

Tests: English, Japanese, other languages (default to 'en'),
       empty/short text, and unicode edge cases.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../worker"))

from pipeline import detect_language


class TestLanguageDetection:

    def test_english_text(self):
        """2.1: English text → returns 'en'."""
        text = (
            "This is a sample document about technology and software engineering. "
            "Python is a popular programming language used for data science and AI."
        )
        assert detect_language(text) == "en"

    def test_japanese_text(self):
        """2.2: Japanese text → returns 'ja'."""
        text = (
            "これはテスト文書です。日本語のテキストを処理するためのサンプルです。"
            "自然言語処理の分野では、日本語特有の形態素解析が重要です。"
        )
        assert detect_language(text) == "ja"

    def test_non_japanese_non_english_defaults_to_en(self):
        """2.3: French text → returns 'en' (non-ja defaults to en)."""
        text = (
            "Ceci est un document de test en français. "
            "La langue française est très belle et expressive."
        )
        assert detect_language(text) == "en"

    def test_empty_text_returns_en(self):
        """2.6: Empty string → returns 'en' (default fallback)."""
        assert detect_language("") == "en"

    def test_very_short_text_returns_en(self):
        """2.7: Very short text 'Hi' → returns 'en' (graceful handling)."""
        assert detect_language("Hi") == "en"

    def test_unicode_emoji_does_not_crash(self):
        """2.8: Emoji-heavy text → returns 'en' (no crash)."""
        text = "🎉🚀💡🌟🔥 Hello World 🎊"
        result = detect_language(text)
        assert result in ("en", "ja")  # either is fine, just no crash

    def test_only_uses_first_2000_chars(self):
        """detect_language only reads first 2000 chars — Japanese buried past 2000 → 'en'."""
        # Start with lots of English, then Japanese
        prefix = "This is English text. " * 100  # ~2200 chars
        suffix = "これは日本語のテキストです。" * 50
        combined = prefix + suffix
        result = detect_language(combined)
        # The first 2000 chars are English, so result should be 'en'
        assert result == "en"
