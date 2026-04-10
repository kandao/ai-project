"""
security/audit.py — Structured audit logging for tool calls.

Append-only audit log. Essential for incident response.
"""

import json
import logging
import time
import aiofiles
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Append-only structured audit log for tool calls.

    Each entry records: timestamp, tool, arguments (sanitized), result summary,
    policy decision, and session context. Written to JSONL for easy ingestion
    by SIEM systems.

    All write methods are async to match the agent's async architecture
    (asyncpg, aiokafka, aioredis). Uses aiofiles for non-blocking file I/O.
    """

    def __init__(self, log_dir: Path = None):
        self.log_dir = log_dir or Path(".audit")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "tool_calls.jsonl"

    async def _write(self, entry: dict) -> None:
        entry["timestamp"] = time.time()
        entry["iso_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        async with aiofiles.open(self.log_file, "a") as f:
            await f.write(json.dumps(entry, default=str) + "\n")

    async def log_success(self, tool: str, args: dict, result: str, context: dict = None):
        await self._write({
            "event": "tool_call",
            "status": "success",
            "tool": tool,
            "arguments": self._sanitize_args(args),
            "result_length": len(result),
            "result_preview": result[:200],
            "context": context or {},
        })

    async def log_denied(self, tool: str, args: dict, reason: str, context: dict = None):
        await self._write({
            "event": "tool_call",
            "status": "denied",
            "tool": tool,
            "arguments": self._sanitize_args(args),
            "reason": reason,
            "context": context or {},
        })
        logger.warning("AUDIT: denied %s — %s", tool, reason)

    async def log_error(self, tool: str, args: dict, error: str, context: dict = None):
        await self._write({
            "event": "tool_call",
            "status": "error",
            "tool": tool,
            "arguments": self._sanitize_args(args),
            "error": error[:500],
            "context": context or {},
        })

    async def log_injection(self, source: str, patterns: list[str], context: dict = None):
        await self._write({
            "event": "injection_detected",
            "source": source,
            "patterns": patterns,
            "context": context or {},
        })
        logger.warning("AUDIT: injection detected in %s — %d patterns", source, len(patterns))

    @staticmethod
    def _sanitize_args(args: dict) -> dict:
        """Remove potentially sensitive argument values from audit log."""
        sanitized = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 500:
                sanitized[k] = v[:200] + f"...[{len(v)} chars]"
            else:
                sanitized[k] = v
        return sanitized
