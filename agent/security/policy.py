"""
security/policy.py — Policy engine for tool call validation.

The policy engine is the central authority for what the agent can and cannot do.
It operates outside the LLM — the LLM cannot modify, bypass, or influence policies.
"""

import os
import re
import time
import logging
from pathlib import Path
from collections import defaultdict
from security.config import TOOL_POLICIES, ARGUMENT_RULES

logger = logging.getLogger(__name__)


class PolicyViolation(Exception):
    """Raised when a tool call violates security policy."""
    pass


class PolicyEngine:
    def __init__(self, mode: str = "kafka"):
        self.mode = mode
        self.policy = TOOL_POLICIES[mode]
        self.call_counts: dict[str, int] = defaultdict(int)
        self.minute_window: list[float] = []
        self.session_calls = 0

    def validate_tool_call(self, tool_name: str, arguments: dict) -> None:
        """
        Validate a tool call against all policies.
        Raises PolicyViolation if any check fails.
        """
        self._check_allowlist(tool_name)
        self._check_rate_limit()
        self._check_arguments(tool_name, arguments)

    def _check_allowlist(self, tool_name: str) -> None:
        if tool_name in self.policy["denied_tools"]:
            raise PolicyViolation(
                f"Tool '{tool_name}' is denied in {self.mode} mode"
            )
        if tool_name not in self.policy["allowed_tools"]:
            raise PolicyViolation(
                f"Tool '{tool_name}' is not in the allowlist for {self.mode} mode"
            )

    def _check_rate_limit(self) -> None:
        now = time.time()
        # Session limit
        self.session_calls += 1
        max_session = self.policy["max_tool_calls_per_session"]
        if self.session_calls > max_session:
            raise PolicyViolation(
                f"Session tool call limit exceeded ({max_session})"
            )
        # Per-minute limit
        self.minute_window = [t for t in self.minute_window if now - t < 60]
        self.minute_window.append(now)
        max_minute = self.policy["max_tool_calls_per_minute"]
        if len(self.minute_window) > max_minute:
            raise PolicyViolation(
                f"Tool call rate limit exceeded ({max_minute}/min)"
            )

    def _check_arguments(self, tool_name: str, arguments: dict) -> None:
        rules = ARGUMENT_RULES.get(tool_name, {})
        for arg_name, constraints in rules.items():
            value = arguments.get(arg_name)
            if value is None:
                continue

            # Type check
            expected_type = constraints.get("type")
            if expected_type == "string" and not isinstance(value, str):
                raise PolicyViolation(
                    f"{tool_name}.{arg_name}: expected string, got {type(value).__name__}"
                )
            if expected_type == "integer" and not isinstance(value, int):
                raise PolicyViolation(
                    f"{tool_name}.{arg_name}: expected integer, got {type(value).__name__}"
                )

            # String constraints
            if isinstance(value, str):
                max_len = constraints.get("max_length")
                if max_len and len(value) > max_len:
                    raise PolicyViolation(
                        f"{tool_name}.{arg_name}: exceeds max length ({max_len})"
                    )
                for pattern in constraints.get("deny_patterns", []):
                    if re.search(pattern, value):
                        raise PolicyViolation(
                            f"{tool_name}.{arg_name}: matches denied pattern"
                        )
                for pattern in constraints.get("require_patterns", []):
                    if not re.search(pattern, value):
                        raise PolicyViolation(
                            f"{tool_name}.{arg_name}: does not match required pattern"
                        )

            # Numeric constraints
            if isinstance(value, (int, float)):
                if "min" in constraints and value < constraints["min"]:
                    raise PolicyViolation(
                        f"{tool_name}.{arg_name}: below minimum ({constraints['min']})"
                    )
                if "max" in constraints and value > constraints["max"]:
                    raise PolicyViolation(
                        f"{tool_name}.{arg_name}: exceeds maximum ({constraints['max']})"
                    )

            # Path sandboxing — resolved path must be under WORKDIR
            if isinstance(value, str) and constraints.get("must_resolve_under") == "WORKDIR":
                resolved = Path(os.path.expanduser(value)).resolve()
                workdir = Path.cwd().resolve()
                if not resolved.is_relative_to(workdir):
                    raise PolicyViolation(
                        f"{tool_name}.{arg_name}: path resolves outside working directory"
                    )
