"""
llm_client.py — Provider-agnostic LLM abstraction.

Supports Anthropic and OpenAI.  All agent logic calls chat() or stream()
— never imports anthropic or openai directly.

Env vars:
    LLM_PROVIDER       "anthropic" (default) or "openai"
    LLM_MODEL          optional model override
    ANTHROPIC_API_KEY / OPENAI_API_KEY
    ANTHROPIC_BASE_URL optional — override Anthropic endpoint (e.g. MiniMax Anthropic-compatible)
    OPENAI_BASE_URL    optional — override OpenAI endpoint (e.g. https://api.minimax.io/v1)
"""

import os
from typing import Generator

import anthropic
import openai

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")

DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}
MODEL = os.getenv("LLM_MODEL") or DEFAULTS.get(PROVIDER, DEFAULTS["anthropic"])

# ── clients (lazy) ────────────────────────────────────────────────────
_anthropic_client = None
_openai_client = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        kwargs = {}
        base_url = os.getenv("ANTHROPIC_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        _anthropic_client = anthropic.Anthropic(**kwargs)
    return _anthropic_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        kwargs = {}
        base_url = os.getenv("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        _openai_client = openai.OpenAI(**kwargs)
    return _openai_client


# ── Anthropic ─────────────────────────────────────────────────────────
def _anthropic_chat(messages: list[dict], system: str = "",
                    tools: list | None = None, max_tokens: int = 8000) -> dict:
    """Returns raw Anthropic response object."""
    kwargs = dict(model=MODEL, messages=messages, max_tokens=max_tokens)
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    return _get_anthropic().messages.create(**kwargs)


def _anthropic_stream(messages: list[dict], system: str = "",
                      max_tokens: int = 8000) -> Generator[str, None, None]:
    kwargs = dict(model=MODEL, messages=messages, max_tokens=max_tokens)
    if system:
        kwargs["system"] = system
    with _get_anthropic().messages.stream(**kwargs) as stream:
        for text in stream.text_stream:
            yield text


# ── OpenAI ────────────────────────────────────────────────────────────
def _openai_to_messages(messages: list[dict], system: str) -> list[dict]:
    """Convert Anthropic-style messages to OpenAI format."""
    oai = []
    if system:
        oai.append({"role": "system", "content": system})
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        # Simple text
        if isinstance(content, str):
            oai.append({"role": role, "content": content})
            continue
        # Tool results / multi-part → flatten to text
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "tool_result":
                        parts.append(f"[tool_result:{part.get('tool_use_id','')}] {part.get('content','')}")
                    elif hasattr(part, "text"):
                        parts.append(part.text)
                    elif part.get("type") == "text":
                        parts.append(part.get("text", ""))
                elif hasattr(part, "text"):
                    parts.append(part.text)
            oai.append({"role": role, "content": "\n".join(parts)})
            continue
        oai.append({"role": role, "content": str(content)})
    return oai


def _openai_tools(tools: list | None) -> list | None:
    """Convert Anthropic tool format to OpenAI function-calling format."""
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        }
        for t in tools
    ]


def _openai_chat(messages: list[dict], system: str = "",
                 tools: list | None = None, max_tokens: int = 8000) -> dict:
    client = _get_openai()
    oai_msgs = _openai_to_messages(messages, system)
    kwargs = dict(model=MODEL, messages=oai_msgs, max_tokens=max_tokens)
    oai_tools = _openai_tools(tools)
    if oai_tools:
        kwargs["tools"] = oai_tools
    return client.chat.completions.create(**kwargs)


def _openai_stream(messages: list[dict], system: str = "",
                   max_tokens: int = 8000) -> Generator[str, None, None]:
    client = _get_openai()
    oai_msgs = _openai_to_messages(messages, system)
    resp = client.chat.completions.create(
        model=MODEL, messages=oai_msgs, max_tokens=max_tokens, stream=True,
    )
    for chunk in resp:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


# ── Public API ────────────────────────────────────────────────────────
def chat(messages: list[dict], system: str = "",
         tools: list | None = None, max_tokens: int = 8000):
    """Send messages to the configured LLM. Returns provider-native response."""
    if PROVIDER == "openai":
        return _openai_chat(messages, system, tools, max_tokens)
    return _anthropic_chat(messages, system, tools, max_tokens)


def stream(messages: list[dict], system: str = "",
           max_tokens: int = 8000) -> Generator[str, None, None]:
    """Streaming text generation. Yields text chunks."""
    if PROVIDER == "openai":
        yield from _openai_stream(messages, system, max_tokens)
    else:
        yield from _anthropic_stream(messages, system, max_tokens)
