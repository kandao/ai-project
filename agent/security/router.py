"""
security/router.py — Trusted kernel for tool execution.

All tool calls flow through the router:
  LLM → Router.execute() → Policy check → Handler → Output filter → Result
"""

import asyncio
import inspect
import logging
from security.policy import PolicyEngine, PolicyViolation
from security.audit import AuditLogger
from security.filters import sanitize_output

logger = logging.getLogger(__name__)


class ToolRouter:
    """
    Trusted kernel for tool execution.

    All tool calls flow through the router:
      LLM → Router.execute() → Policy check → Handler → Output filter → Result

    The router is the ONLY component that calls tool handlers.

    Supports both sync and async handlers — if the handler is a coroutine,
    it is awaited; otherwise it is called directly.
    """

    def __init__(
        self,
        handlers: dict,
        policy: PolicyEngine,
        audit: AuditLogger,
    ):
        self.handlers = handlers
        self.policy = policy
        self.audit = audit

    async def execute(self, tool_name: str, arguments: dict, context: dict = None) -> str:
        """
        Execute a tool call through the full security pipeline.

        Args:
            tool_name:  Name of the tool to call.
            arguments:  Arguments from the LLM.
            context:    Session context (user_id, session_id, mode).

        Returns:
            Sanitized tool result string.
        """
        # 1. Policy enforcement
        try:
            self.policy.validate_tool_call(tool_name, arguments)
        except PolicyViolation as e:
            await self.audit.log_denied(tool_name, arguments, str(e), context)
            logger.warning("Policy denied: %s(%s) — %s", tool_name, arguments, e)
            return f"Policy violation: {e}"

        # 2. Execute handler (supports both sync and async)
        handler = self.handlers.get(tool_name)
        if not handler:
            await self.audit.log_denied(tool_name, arguments, "unknown tool", context)
            return f"Unknown tool: {tool_name}"

        try:
            if inspect.iscoroutinefunction(handler):
                raw_result = await handler(**arguments)
            else:
                raw_result = handler(**arguments)
        except Exception as e:
            await self.audit.log_error(tool_name, arguments, str(e), context)
            logger.error("Tool error: %s — %s", tool_name, e)
            return f"Error: {e}"

        # 3. Output filtering
        result = sanitize_output(raw_result, tool_name)

        # 4. Audit log
        await self.audit.log_success(tool_name, arguments, result, context)

        return result
