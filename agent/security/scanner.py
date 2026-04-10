"""
security/scanner.py — Injection detection for inputs + RAG content.

Scans user input and RAG-retrieved content for known injection patterns
before they reach the LLM.
"""

import re
import logging

logger = logging.getLogger(__name__)


# Patterns indicating prompt injection attempts
INJECTION_PATTERNS = [
    # Direct instruction override
    r"(?i)ignore\s+(all\s+)?previous\s+instructions",
    r"(?i)ignore\s+(all\s+)?above\s+instructions",
    r"(?i)disregard\s+(all\s+)?previous",
    r"(?i)forget\s+(all\s+)?previous",
    r"(?i)override\s+(system\s+)?prompt",
    r"(?i)new\s+instructions?\s*:",

    # Role hijacking
    r"(?i)you\s+are\s+now\s+a",
    r"(?i)act\s+as\s+if\s+you\s+are",
    r"(?i)pretend\s+you\s+are",
    r"(?i)switch\s+to\s+.+\s+mode",

    # Tool manipulation
    r"(?i)call\s+.+\s+for\s+all\s+users",
    r"(?i)execute\s+.+\s+on\s+every",
    r"(?i)send\s+(this|the\s+data|results)\s+to\s+(http|https|ftp)",
    r"(?i)(curl|wget|fetch)\s+https?://",

    # Data exfiltration
    r"(?i)list\s+all\s+users",
    r"(?i)show\s+(me\s+)?all\s+(the\s+)?passwords",
    r"(?i)dump\s+(the\s+)?(database|table|schema)",
    r"(?i)export\s+all\s+data",

    # Hidden instruction markers
    r"<\s*system\s*>",
    r"\[INST\]",
    r"###\s*SYSTEM",
]

COMPILED_PATTERNS = [re.compile(p) for p in INJECTION_PATTERNS]


class InjectionDetected(Exception):
    """Raised when a prompt injection is detected."""
    def __init__(self, source: str, pattern: str, text_snippet: str):
        self.source = source
        self.pattern = pattern
        self.text_snippet = text_snippet
        super().__init__(f"Injection detected in {source}: matched '{pattern}'")


def scan_text(text: str, source: str = "input") -> list[str]:
    """
    Scan text for injection patterns. Returns list of matched pattern strings.
    Does not raise — caller decides how to handle.
    """
    matches = []
    for i, pattern in enumerate(COMPILED_PATTERNS):
        if pattern.search(text):
            matches.append(INJECTION_PATTERNS[i])
    if matches:
        logger.warning(
            "Injection patterns detected in %s: %d matches", source, len(matches)
        )
    return matches


def scan_user_message(message: str) -> list[str]:
    """Scan a user's chat message for injection patterns."""
    return scan_text(message, source="user_message")


def scan_rag_content(chunks: list[str]) -> list[tuple[int, list[str]]]:
    """
    Scan RAG-retrieved chunks for injection patterns.

    Returns list of (chunk_index, matched_patterns) for flagged chunks.
    Flagged chunks should be excluded or marked before injecting into LLM context.
    """
    flagged = []
    for i, chunk in enumerate(chunks):
        matches = scan_text(chunk, source=f"rag_chunk_{i}")
        if matches:
            flagged.append((i, matches))
    return flagged


def sanitize_rag_chunks(chunks: list[str]) -> list[str]:
    """
    Remove or neutralize flagged RAG chunks before LLM injection.

    Flagged chunks are replaced with a warning marker so the LLM knows
    content was removed, but cannot see the injected instructions.
    """
    clean = []
    for i, chunk in enumerate(chunks):
        matches = scan_text(chunk, source=f"rag_chunk_{i}")
        if matches:
            clean.append(
                f"[CONTENT REMOVED: chunk {i} flagged by security scanner]"
            )
        else:
            clean.append(chunk)
    return clean
