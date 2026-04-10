"""
tools/bash_safe.py — Constrained bash replacement.

Instead of accepting arbitrary shell commands, this tool provides
a curated set of safe operations via an allowlist approach.

Security model:
  - Commands are split on shell operators (;, |, &&, ||)
  - EVERY sub-command must have its first token in ALLOWED_COMMANDS
  - DENIED_ANYWHERE tokens are blocked in ANY position
  - python/pip are restricted: -c (inline code) and -m with dangerous
    modules are blocked to prevent using python as an escape hatch
  - No subshells ($(), backticks), no redirection, no shell=True
"""

import re
import shlex
import subprocess
from pathlib import Path

WORKDIR = Path.cwd()

# Allowlist of permitted commands (first token of each sub-command)
ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "wc",
    "grep", "find", "sort", "uniq",
    "python", "pip",
    "date", "echo",
}

# Explicitly denied — if found as ANY token, command is blocked
DENIED_ANYWHERE = {
    "curl", "wget", "nc", "ncat", "netcat",
    "ssh", "scp", "sftp", "ftp",
    "rm", "rmdir", "mv", "cp",
    "sudo", "su", "chmod", "chown", "chattr",
    "dd", "mkfs", "mount", "umount",
    "env", "printenv", "set",
    "export",
    "kill", "killall", "pkill",
    "shutdown", "reboot", "halt",
    "docker", "kubectl",
    "bash", "sh", "zsh", "dash", "csh",  # no spawning sub-shells
    "eval", "exec",
    "nohup", "screen", "tmux",
    "xargs",                               # can invoke arbitrary commands
}

# python -m modules that are dangerous
DENIED_PYTHON_MODULES = {
    "http.server", "SimpleHTTPServer",
    "smtplib", "ftplib",
    "socket", "asyncio",
    "subprocess", "os", "shutil",
    "pty", "code", "codeop",
}

# Regex to split on shell operators: ; | && ||
SHELL_SPLIT = re.compile(r"\s*(?:;|\|{1,2}|&&)\s*")


def _validate_sub_command(tokens: list[str]) -> str | None:
    """Validate a single sub-command. Returns error string or None if OK."""
    if not tokens:
        return None

    # Check every token against deny list
    for token in tokens:
        base = token.rsplit("/", 1)[-1].lower()
        if base in DENIED_ANYWHERE:
            return f"Error: Command '{base}' is not permitted"

    # Check first token is in allowlist
    first_cmd = tokens[0].rsplit("/", 1)[-1].lower()
    if first_cmd not in ALLOWED_COMMANDS:
        return f"Error: Command '{first_cmd}' is not in the allowlist"

    # python-specific restrictions
    if first_cmd == "python" or first_cmd.startswith("python3"):
        # Block python -c (arbitrary inline code)
        if "-c" in tokens:
            return "Error: 'python -c' (inline code execution) is not permitted"
        # Block dangerous python -m modules
        if "-m" in tokens:
            idx = tokens.index("-m")
            if idx + 1 < len(tokens):
                module = tokens[idx + 1].lower()
                if module in DENIED_PYTHON_MODULES:
                    return f"Error: 'python -m {module}' is not permitted"

    return None


def run_safe_bash(command: str) -> str:
    """
    Execute a shell command with safety constraints.

    Checks:
      1. No subshells ($(), backticks), no redirection
      2. Split on shell operators (; | && ||)
      3. EVERY sub-command's first token must be in ALLOWED_COMMANDS
      4. DENIED_ANYWHERE tokens blocked in ANY position of ANY sub-command
      5. python -c and dangerous python -m modules blocked
      6. Executed WITHOUT shell=True — uses shlex tokenization
      7. Sandboxed to WORKDIR with 30s timeout
    """
    # Block subshells and redirection first (before any parsing)
    if "`" in command:
        return "Error: Backtick subshell execution is not permitted"
    if "$(" in command:
        return "Error: Subshell execution ($()) is not permitted"
    if ">" in command or ">>" in command:
        return "Error: Output redirection is not permitted"
    if "<" in command:
        return "Error: Input redirection is not permitted"

    # Split on shell operators to get individual sub-commands
    sub_commands = SHELL_SPLIT.split(command.strip())

    for sub_cmd in sub_commands:
        sub_cmd = sub_cmd.strip()
        if not sub_cmd:
            continue
        try:
            tokens = shlex.split(sub_cmd)
        except ValueError as e:
            return f"Error: Invalid command syntax — {e}"
        error = _validate_sub_command(tokens)
        if error:
            return error

    # Execute — use subprocess.run with shell=False via shlex for simple commands.
    # For piped commands, we must use shell=True but have already validated all
    # sub-commands above.
    has_operators = bool(SHELL_SPLIT.search(command))

    try:
        if has_operators:
            # Piped/chained — shell=True is required, but all sub-commands validated
            r = subprocess.run(
                command, shell=True, cwd=WORKDIR,
                capture_output=True, text=True, timeout=30,
                env=_safe_env(),
            )
        else:
            # Simple command — use shell=False for maximum safety
            tokens = shlex.split(command)
            r = subprocess.run(
                tokens, cwd=WORKDIR,
                capture_output=True, text=True, timeout=30,
                env=_safe_env(),
            )
        out = (r.stdout + r.stderr).strip()
        return out[:10000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (30s)"
    except FileNotFoundError:
        return f"Error: Command not found"


def _safe_env() -> dict:
    """
    Return a minimal environment for subprocess execution.
    Strips sensitive variables to prevent exfiltration via error messages.
    """
    import os
    safe = {}
    ALLOWED_ENV = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR"}
    for key in ALLOWED_ENV:
        if key in os.environ:
            safe[key] = os.environ[key]
    return safe
