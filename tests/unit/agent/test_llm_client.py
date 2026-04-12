"""
Unit tests for agent/llm/llm_client.py

Tests: provider dispatch, chat() routing, model selection.
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestLLMClientDispatch:

    def test_anthropic_provider_selected_by_default(self):
        """8.1: LLM_PROVIDER=anthropic → _anthropic_chat is called."""
        # Reload the module with anthropic provider
        import importlib
        import llm.llm_client as llm_mod

        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_anthropic.return_value.messages.create.return_value = mock_response

        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}):
            with patch("anthropic.Anthropic", mock_anthropic):
                # Reset singleton
                llm_mod._anthropic_client = None
                result = llm_mod._anthropic_chat(
                    messages=[{"role": "user", "content": "hello"}],
                    max_tokens=100,
                )

        mock_anthropic.return_value.messages.create.assert_called_once()
        assert result == mock_response

    def test_openai_provider_selected(self):
        """8.2: LLM_PROVIDER=openai → _openai_chat is called."""
        import llm.llm_client as llm_mod

        mock_openai = MagicMock()
        mock_response = MagicMock()
        mock_openai.return_value.chat.completions.create.return_value = mock_response

        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}):
            with patch("openai.OpenAI", mock_openai):
                llm_mod._openai_client = None
                result = llm_mod._openai_chat(
                    messages=[{"role": "user", "content": "hello"}],
                    max_tokens=100,
                )

        mock_openai.return_value.chat.completions.create.assert_called_once()
        assert result == mock_response

    def test_chat_routes_to_anthropic(self):
        """chat() with PROVIDER=anthropic calls _anthropic_chat."""
        import llm.llm_client as llm_mod

        with patch.object(llm_mod, "PROVIDER", "anthropic"):
            with patch.object(llm_mod, "_anthropic_chat") as mock_chat:
                mock_chat.return_value = MagicMock()
                llm_mod.chat(messages=[{"role": "user", "content": "hi"}])
                mock_chat.assert_called_once()

    def test_chat_routes_to_openai(self):
        """chat() with PROVIDER=openai calls _openai_chat."""
        import llm.llm_client as llm_mod

        with patch.object(llm_mod, "PROVIDER", "openai"):
            with patch.object(llm_mod, "_openai_chat") as mock_chat:
                mock_chat.return_value = MagicMock()
                llm_mod.chat(messages=[{"role": "user", "content": "hi"}])
                mock_chat.assert_called_once()

    def test_chat_with_tools_forwarded(self):
        """8.3: chat() with tools → tools forwarded to provider."""
        import llm.llm_client as llm_mod

        tools = [{"name": "read_file", "input_schema": {"type": "object"}}]

        with patch.object(llm_mod, "PROVIDER", "anthropic"):
            with patch.object(llm_mod, "_anthropic_chat") as mock_chat:
                mock_chat.return_value = MagicMock()
                llm_mod.chat(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=tools,
                )
                call_args, call_kwargs = mock_chat.call_args
                # tools may be passed as positional arg (index 2) or keyword arg
                passed_tools = call_kwargs.get("tools") or (call_args[2] if len(call_args) > 2 else None)
                assert passed_tools == tools

    def test_custom_base_url_used(self):
        """8.5: ANTHROPIC_BASE_URL → client created with custom base_url."""
        import llm.llm_client as llm_mod

        mock_anthropic = MagicMock()

        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://custom.api/"}):
            with patch("anthropic.Anthropic", mock_anthropic):
                llm_mod._anthropic_client = None
                llm_mod._get_anthropic()

        call_kwargs = mock_anthropic.call_args[1]
        assert call_kwargs.get("base_url") == "https://custom.api/"
        llm_mod._anthropic_client = None

    def test_default_model_anthropic(self):
        """8.6: No LLM_MODEL set → provider default used (claude-sonnet-4-6)."""
        import llm.llm_client as llm_mod

        # DEFAULTS["anthropic"] should be a non-empty string
        assert llm_mod.DEFAULTS["anthropic"]
        assert "claude" in llm_mod.DEFAULTS["anthropic"]

    def test_default_model_openai(self):
        """8.6 variant: OpenAI default model is gpt-4o."""
        import llm.llm_client as llm_mod

        assert llm_mod.DEFAULTS["openai"] == "gpt-4o"

    def test_model_override_via_env(self):
        """8.7: LLM_MODEL=custom-model → overrides the default MODEL."""
        import importlib
        import llm.llm_client as llm_mod

        with patch.dict(os.environ, {"LLM_MODEL": "custom-model-xyz", "LLM_PROVIDER": "anthropic"}):
            importlib.reload(llm_mod)
            assert llm_mod.MODEL == "custom-model-xyz"

        # Reload to restore original state (patch.dict already restored env)
        importlib.reload(llm_mod)

    def test_stream_anthropic_yields_text(self):
        """8.4: _anthropic_stream yields text chunks from text_stream."""
        import llm.llm_client as llm_mod

        mock_anthropic = MagicMock()
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_stream_ctx.text_stream = iter(["Hello", " ", "world"])
        mock_anthropic.return_value.messages.stream.return_value = mock_stream_ctx

        with patch("anthropic.Anthropic", mock_anthropic):
            llm_mod._anthropic_client = None
            chunks = list(llm_mod._anthropic_stream(
                messages=[{"role": "user", "content": "hi"}]
            ))

        assert chunks == ["Hello", " ", "world"]
        llm_mod._anthropic_client = None

    def test_openai_base_url_used(self):
        """OPENAI_BASE_URL → OpenAI client created with custom base_url."""
        import llm.llm_client as llm_mod

        mock_openai = MagicMock()

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "https://custom-openai.api/v1"}):
            with patch("openai.OpenAI", mock_openai):
                llm_mod._openai_client = None
                llm_mod._get_openai()

        call_kwargs = mock_openai.call_args[1]
        assert call_kwargs.get("base_url") == "https://custom-openai.api/v1"
        llm_mod._openai_client = None
