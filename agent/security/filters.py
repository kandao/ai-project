"""
security/filters.py — Output sanitization for tool results.

Sanitizes tool results before they are returned to the LLM context.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Maximum result size returned to LLM (chars)
MAX_OUTPUT_LENGTH = 10000

# Patterns to redact from tool output
REDACTION_PATTERNS = [
    # API keys and tokens
    (r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", "[REDACTED_CREDENTIAL]"),
    (r"sk-[a-zA-Z0-9]{20,}", "[REDACTED_API_KEY]"),
    (r"otk_[a-zA-Z0-9_-]+", "[REDACTED_TOKEN]"),

    # Connection strings with passwords
    (r"postgresql://[^@]+@", "postgresql://[REDACTED]@"),

    # Email addresses (PII)
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[REDACTED_EMAIL]"),

    # Environment variable dumps
    (r"(?i)^[A-Z_]+=.+$", "[REDACTED_ENV_VAR]"),
]


def sanitize_output(result: str, tool_name: str) -> str:
    """
    Sanitize tool output before returning to LLM.

    1. Truncate to MAX_OUTPUT_LENGTH
    2. Redact sensitive patterns
    3. Tool-specific filtering
    """
    if not isinstance(result, str):
        result = str(result)

    # Truncate
    if len(result) > MAX_OUTPUT_LENGTH:
        result = result[:MAX_OUTPUT_LENGTH] + f"\n[truncated — {len(result)} total chars]"

    # Redact sensitive patterns
    for pattern, replacement in REDACTION_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.MULTILINE)

    return result
