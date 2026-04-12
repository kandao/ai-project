"""
Unit tests for chunk quality properties.

Validates: no content loss, overlap consistency, chunk size bounds,
no empty chunks, monotonic indices, and config override.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../worker"))

import pytest
from chunking.english import chunk_english


class TestChunkQuality:

    def test_no_content_loss(self):
        """10.1: All words from original text appear in some chunk."""
        words = [f"unique_word_{i}" for i in range(200)]
        text = " ".join(words)
        chunks = chunk_english(text, chunk_size=50, overlap=10)

        # Collect all words from all chunks (union, not ordered)
        all_chunk_words = set()
        for c in chunks:
            all_chunk_words.update(c.text.split())

        for w in words:
            assert w in all_chunk_words, f"Word '{w}' lost during chunking"

    def test_overlap_consistency(self):
        """10.2: Overlap regions between adjacent chunks match exactly."""
        words = [f"w{i}" for i in range(100)]
        text = " ".join(words)
        overlap = 10
        chunks = chunk_english(text, chunk_size=30, overlap=overlap)

        for i in range(len(chunks) - 1):
            tail = chunks[i].text.split()[-overlap:]
            head = chunks[i + 1].text.split()[:overlap]
            assert tail == head, (
                f"Chunk {i} tail {tail!r} != Chunk {i+1} head {head!r}"
            )

    def test_chunk_size_bounds(self):
        """10.3: Every chunk has token_count ≤ chunk_size."""
        text = " ".join([f"word{i}" for i in range(500)])
        chunk_size = 64
        chunks = chunk_english(text, chunk_size=chunk_size, overlap=8)
        for i, c in enumerate(chunks):
            assert c.token_count <= chunk_size, (
                f"Chunk {i} has token_count={c.token_count} > chunk_size={chunk_size}"
            )

    def test_no_empty_chunks(self):
        """10.4: No chunk has empty text field."""
        text = " ".join([f"word{i}" for i in range(100)])
        chunks = chunk_english(text, chunk_size=20, overlap=5)
        for i, c in enumerate(chunks):
            assert c.text.strip() != "", f"Chunk {i} is empty"

    def test_index_monotonicity(self):
        """10.5: Chunk indices are 0, 1, 2, ... monotonically increasing."""
        text = " ".join([f"word{i}" for i in range(200)])
        chunks = chunk_english(text, chunk_size=30, overlap=5)
        for expected_idx, c in enumerate(chunks):
            assert c.index == expected_idx, (
                f"Expected index {expected_idx}, got {c.index}"
            )

    def test_config_chunk_size_respected(self):
        """10.6: Custom chunk_size=256, overlap=32 → chunks respect the config."""
        text = " ".join([f"word{i}" for i in range(1000)])
        chunk_size = 256
        overlap = 32
        chunks = chunk_english(text, chunk_size=chunk_size, overlap=overlap)
        for c in chunks:
            assert c.token_count <= chunk_size
        # Check step size: each chunk should start overlap words earlier than the next
        if len(chunks) >= 2:
            step = chunk_size - overlap
            first_words = chunks[0].text.split()
            second_words = chunks[1].text.split()
            # The second chunk's first non-overlapping word should be at position `step`
            assert second_words[:overlap] == first_words[step:step + overlap]
