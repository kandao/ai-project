"""
Unit tests for worker/chunking/japanese.py

Tests: MeCab-based tokenization, overlap, mixed text, empty input.
Requires fugashi + MeCab installed (available in the worker container).
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../worker"))

import pytest

# Skip entire module if fugashi/MeCab not installed
fugashi = pytest.importorskip("fugashi", reason="fugashi not installed")

from chunking.japanese import chunk_japanese


class TestJapaneseChunking:

    def test_basic_japanese_chunking(self):
        """3.9: Japanese paragraph → produces chunks split on morpheme boundaries."""
        text = (
            "日本語の自然言語処理は、形態素解析を基盤としています。"
            "MeCabは広く使われる形態素解析器です。"
            "テキストを適切に分割することが重要です。"
        )
        chunks = chunk_japanese(text, chunk_size=10, overlap=2)
        assert len(chunks) >= 1
        for c in chunks:
            assert c.token_count <= 10

    def test_no_spaces_in_japanese_chunks(self):
        """3.10: Japanese chunks contain no whitespace between tokens (concatenated)."""
        text = "これはテスト文書です。日本語のテキストを処理します。"
        chunks = chunk_japanese(text, chunk_size=5, overlap=1)
        for c in chunks:
            # Japanese tokens joined without spaces
            assert " " not in c.text, f"Unexpected space in chunk: '{c.text}'"

    def test_overlap_correctness_japanese(self):
        """3.11: Last N tokens of chunk K == first N tokens of chunk K+1."""
        text = (
            "自然言語処理の技術は急速に発展しています。"
            "深層学習を使ったモデルが広く普及しています。"
            "日本語の処理には特有の課題があります。"
        )
        overlap = 3
        chunks = chunk_japanese(text, chunk_size=10, overlap=overlap)
        if len(chunks) < 2:
            pytest.skip("Not enough chunks to test overlap")
        for i in range(len(chunks) - 1):
            import fugashi as fg
            tagger = fg.Tagger()
            tail_tokens = [w.surface for w in tagger(chunks[i].text)][-overlap:]
            head_tokens = [w.surface for w in tagger(chunks[i + 1].text)][:overlap]
            assert tail_tokens == head_tokens, (
                f"Overlap mismatch between chunk {i} and {i+1}: "
                f"tail={tail_tokens}, head={head_tokens}"
            )

    def test_empty_japanese_text(self):
        """3.13: Empty string → empty list."""
        chunks = chunk_japanese("", chunk_size=10, overlap=2)
        assert chunks == []

    def test_chunk_indices_monotonic(self):
        """Chunk indices are 0, 1, 2, ... monotonically increasing."""
        text = "テキスト処理の基礎知識として、形態素解析があります。" * 5
        chunks = chunk_japanese(text, chunk_size=10, overlap=2)
        for expected_idx, c in enumerate(chunks):
            assert c.index == expected_idx
