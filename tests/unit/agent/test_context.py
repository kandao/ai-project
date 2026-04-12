"""
Unit tests for context compaction functions in agent/loop.py

Tests: estimate_tokens, microcompact, auto_compact.
"""

import json
import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock
from loop import estimate_tokens, microcompact, auto_compact


def make_tool_result_msg(content: str, tool_id: str = "tool_1") -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": content}
        ],
    }


def make_text_msg(text: str, role: str = "assistant") -> dict:
    return {"role": role, "content": text}


class TestEstimateTokens:

    def test_estimate_is_approx_len_over_4(self):
        """6.1: estimate_tokens returns roughly len(json_str) / 4."""
        msgs = [{"role": "user", "content": "Hello"}]
        serialized = json.dumps(msgs, default=str)
        estimate = estimate_tokens(msgs)
        assert estimate == len(serialized) // 4

    def test_empty_messages_returns_small_value(self):
        """Empty messages list → small estimate (just the serialized brackets)."""
        estimate = estimate_tokens([])
        assert estimate < 10


class TestMicrocompact:

    def test_three_or_fewer_results_not_cleared(self):
        """6.2: ≤3 tool_result blocks → no clearing."""
        msgs = [make_tool_result_msg("Long result content " * 10, f"t{i}") for i in range(3)]
        original_contents = [
            msgs[i]["content"][0]["content"] for i in range(3)
        ]
        microcompact(msgs)
        for i, msg in enumerate(msgs):
            assert msg["content"][0]["content"] == original_contents[i]

    def test_more_than_three_results_clears_old_long_ones(self):
        """6.3: 6 tool_result blocks → first 3 long ones cleared to '[cleared]'."""
        msgs = [
            make_tool_result_msg("x" * 200, f"t{i}") for i in range(6)
        ]
        microcompact(msgs)
        # First 3 should be cleared (long content)
        for msg in msgs[:3]:
            assert msg["content"][0]["content"] == "[cleared]"
        # Last 3 should be preserved
        for msg in msgs[3:]:
            assert msg["content"][0]["content"] == "x" * 200

    def test_short_results_not_cleared_even_if_old(self):
        """6.4: Short tool results (< 100 chars) are not cleared even if old."""
        msgs = [
            make_tool_result_msg("short", f"t{i}") for i in range(6)
        ]
        microcompact(msgs)
        # All should still have "short" (content is < 100 chars)
        for msg in msgs:
            assert msg["content"][0]["content"] == "short"


class TestAutoCompact:

    def test_transcript_saved(self, tmp_path):
        """6.5: auto_compact → transcript JSONL file created."""
        import loop
        original_dir = loop.TRANSCRIPT_DIR
        loop.TRANSCRIPT_DIR = tmp_path

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Summary of conversation")]

        with patch("loop.chat", return_value=mock_resp):
            messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
            auto_compact(messages, session_id="test123")

        transcript_files = list(tmp_path.glob("transcript_*.jsonl"))
        assert len(transcript_files) == 1

        loop.TRANSCRIPT_DIR = original_dir

    def test_auto_compact_returns_summary_message(self, tmp_path):
        """6.6: auto_compact → returns list with 1 message containing summary."""
        import loop
        original_dir = loop.TRANSCRIPT_DIR
        loop.TRANSCRIPT_DIR = tmp_path

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="This is the summary")]

        with patch("loop.chat", return_value=mock_resp):
            messages = [{"role": "user", "content": "Question"}]
            result = auto_compact(messages)

        assert len(result) == 1
        assert "This is the summary" in result[0]["content"]

        loop.TRANSCRIPT_DIR = original_dir
