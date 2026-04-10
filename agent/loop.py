"""
loop.py — Core ReAct loop for Agent01.

Mechanisms (extracted from s_full.py):
  1. tool_use       — dispatch tool calls from LLM responses
  2. todo_write     — TodoManager for short task checklists
  3. skill_loading  — SkillLoader for dynamic prompt injection
  4. context_compact — microcompact + auto_compact when context grows
  5. background     — BackgroundManager for async shell commands

The loop runs: Reason → Act → Observe → repeat until stop.
"""

import asyncio
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue

from llm.llm_client import chat, MODEL
from tools import TOOLS as DOMAIN_TOOLS, TOOL_HANDLERS as DOMAIN_HANDLERS
from tools.bash_safe import run_safe_bash
from security.policy import PolicyEngine
from security.router import ToolRouter
from security.audit import AuditLogger

logger = logging.getLogger(__name__)

WORKDIR = Path.cwd()
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOKEN_THRESHOLD = 150000
MAX_LOOP_ITERATIONS = 50


# ═══════════════════════════════════════════════════════════════════════
# 1. BASE TOOLS — file I/O + bash_safe (no unrestricted bash)
# ═══════════════════════════════════════════════════════════════════════

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# ═══════════════════════════════════════════════════════════════════════
# 2. TODO MANAGER
# ═══════════════════════════════════════════════════════════════════════

class TodoManager:
    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        validated, ip = [], 0
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()
            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not af:
                raise ValueError(f"Item {i}: activeForm required")
            if status == "in_progress":
                ip += 1
            validated.append({"content": content, "status": status, "activeForm": af})
        if len(validated) > 20:
            raise ValueError("Max 20 todos")
        if ip > 1:
            raise ValueError("Only one in_progress allowed")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            m = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(item["status"], "[?]")
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{m} {item['content']}{suffix}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        return any(item.get("status") != "completed" for item in self.items)


# ═══════════════════════════════════════════════════════════════════════
# 3. SKILL LOADER
# ═══════════════════════════════════════════════════════════════════════

class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills = {}
        if skills_dir.exists():
            for f in sorted(skills_dir.rglob("SKILL.md")):
                text = f.read_text()
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip()
                    body = match.group(2).strip()
                name = meta.get("name", f.parent.name)
                self.skills[name] = {"meta": meta, "body": body}

    def descriptions(self) -> str:
        if not self.skills:
            return "(no skills loaded)"
        return "\n".join(
            f"  - {n}: {s['meta'].get('description', '-')}"
            for n, s in self.skills.items()
        )

    def load(self, name: str) -> str:
        s = self.skills.get(name)
        if not s:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{s['body']}\n</skill>"


# ═══════════════════════════════════════════════════════════════════════
# 4. CONTEXT COMPACTION
# ═══════════════════════════════════════════════════════════════════════

def estimate_tokens(messages: list) -> int:
    """Rough token estimate. Assumes ~4 chars per token (heuristic, not exact)."""
    return len(json.dumps(messages, default=str)) // 4


def microcompact(messages: list):
    """Clear old tool results to save context space."""
    tool_result_parts = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_result_parts.append(part)
    if len(tool_result_parts) <= 3:
        return
    for part in tool_result_parts[:-3]:
        if isinstance(part.get("content"), str) and len(part["content"]) > 100:
            part["content"] = "[cleared]"


def auto_compact(messages: list, session_id: str = None) -> list:
    """Save transcript to disk and summarize for continuity."""
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    tag = session_id or "local"
    path = TRANSCRIPT_DIR / f"transcript_{tag}_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    conv_text = json.dumps(messages, default=str)[-80000:]
    resp = chat(
        messages=[{"role": "user", "content": f"Summarize for continuity:\n{conv_text}"}],
        max_tokens=2000,
    )
    summary = resp.content[0].text
    return [
        {"role": "user", "content": f"[Compressed. Transcript: {path}]\n{summary}"},
    ]


# ═══════════════════════════════════════════════════════════════════════
# 5. BACKGROUND MANAGER
# ═══════════════════════════════════════════════════════════════════════

class BackgroundManager:
    def __init__(self):
        self.tasks = {}
        self.notifications = Queue()
        self._lock = threading.Lock()

    def run(self, command: str, timeout: int = 120) -> str:
        import uuid
        tid = str(uuid.uuid4())[:8]
        with self._lock:
            self.tasks[tid] = {"status": "running", "command": command, "result": None}
        threading.Thread(target=self._exec, args=(tid, command, timeout), daemon=True).start()
        return f"Background task {tid} started: {command[:80]}"

    def _exec(self, tid: str, command: str, timeout: int):
        # Route through bash_safe for security — no unrestricted shell
        output = run_safe_bash(command)
        status = "error" if output.startswith("Error:") else "completed"
        with self._lock:
            self.tasks[tid].update({"status": status, "result": output})
        self.notifications.put({
            "task_id": tid,
            "status": status,
            "result": output[:500],
        })

    def check(self, tid: str = None) -> str:
        with self._lock:
            if tid:
                t = self.tasks.get(tid)
                return f"[{t['status']}] {t.get('result') or '(running)'}" if t else f"Unknown: {tid}"
            return "\n".join(
                f"{k}: [{v['status']}] {v['command'][:60]}"
                for k, v in self.tasks.items()
            ) or "No background tasks."

    def drain(self) -> list:
        notifs = []
        while not self.notifications.empty():
            notifs.append(self.notifications.get_nowait())
        return notifs


# ═══════════════════════════════════════════════════════════════════════
# SKILL LOADER (module-level, read-only after init)
# ═══════════════════════════════════════════════════════════════════════

SKILLS_DIR = WORKDIR / "skills"
SKILLS = SkillLoader(SKILLS_DIR)


# ═══════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS — combine base tools + domain tools
# ═══════════════════════════════════════════════════════════════════════

BASE_TOOLS = [
    {"name": "bash_safe", "description": "Run a constrained shell command (allowlisted commands only).",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "TodoWrite", "description": "Update task tracking list. Use for multi-step work.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}, "activeForm": {"type": "string"}}, "required": ["content", "status", "activeForm"]}}}, "required": ["items"]}},
    {"name": "load_skill", "description": "Load specialized knowledge by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "compress", "description": "Manually compress conversation context.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "background_run", "description": "Run a shell command in a background thread (uses bash_safe constraints).",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
]

ALL_TOOLS = BASE_TOOLS + DOMAIN_TOOLS


def _build_system_prompt() -> str:
    return f"""You are an AI business analyst agent at {WORKDIR}.

You can analyze CSV files, query databases, look up stock prices, extract text from PDF/DOCX files, and generate charts.
Use tools to solve tasks step by step. Use TodoWrite for multi-step work to track progress.
Use load_skill for specialized knowledge. Use background_run for long-running commands.

Available skills:
{SKILLS.descriptions()}"""


# ═══════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT (kept as module-level constant name for consumer.py import)
# ═══════════════════════════════════════════════════════════════════════

SYSTEM = _build_system_prompt()


# ═══════════════════════════════════════════════════════════════════════
# REACT LOOP
# ═══════════════════════════════════════════════════════════════════════

async def agent_loop(messages: list, tools: list = None, handlers: dict = None, security_context: dict = None):
    """
    Core ReAct loop. Runs until the LLM stops calling tools.

    Args:
        messages:          Conversation history (mutated in-place).
        tools:             Optional tool schemas list. Defaults to ALL_TOOLS.
        handlers:          Optional handler dict. Defaults to ALL_HANDLERS.
        security_context:  Optional dict with mode, session_id, user_id, etc.

    Before each LLM call:
      - microcompact old tool results
      - auto_compact if context too large
      - drain background task notifications
    """
    # Per-session mutable instances (not shared across sessions)
    todo = TodoManager()
    bg = BackgroundManager()

    # Use provided tools/handlers or fall back to module-level defaults
    _tools = tools if tools is not None else ALL_TOOLS

    # Build per-session handlers with session-scoped TODO and BG
    base_handlers = {
        "bash_safe":        lambda **kw: run_safe_bash(kw["command"]),
        "read_file":        lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "TodoWrite":        lambda **kw: todo.update(kw["items"]),
        "load_skill":       lambda **kw: SKILLS.load(kw["name"]),
        "compress":         lambda **kw: "(context will be compressed after this tool round)",
        "background_run":   lambda **kw: bg.run(kw["command"], kw.get("timeout", 120)),
        "check_background": lambda **kw: bg.check(kw.get("task_id")),
    }

    if handlers is not None:
        # Caller-provided handlers (e.g., scoped DB handlers from consumer.py)
        # merge on top of per-session base handlers
        _handlers = {**base_handlers, **DOMAIN_HANDLERS, **handlers}
    else:
        _handlers = {**base_handlers, **DOMAIN_HANDLERS}

    # Initialize security components
    mode = security_context.get("mode", "cli") if security_context else "cli"
    session_id = security_context.get("session_id") if security_context else None
    policy = PolicyEngine(mode=mode)
    audit = AuditLogger()
    router = ToolRouter(_handlers, policy, audit)

    # Filter tool schemas to only show allowed tools to LLM
    allowed = set(policy.policy["allowed_tools"])
    _tools = [t for t in _tools if t["name"] in allowed]

    rounds_without_todo = 0
    iteration = 0

    while iteration < MAX_LOOP_ITERATIONS:
        iteration += 1

        # — Mechanism 4: compression pipeline —
        microcompact(messages)
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            logger.info("auto-compact triggered (iteration %d)", iteration)
            messages[:] = auto_compact(messages, session_id=session_id)

        # — Mechanism 5: drain background notifications —
        notifs = bg.drain()
        if notifs:
            txt = "\n".join(
                f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
            )
            messages.append({
                "role": "user",
                "content": f"<background-results>\n{txt}\n</background-results>",
            })

        # — LLM call (blocking → run in thread to avoid blocking event loop) —
        response = await asyncio.to_thread(
            chat,
            messages=messages, system=SYSTEM,
            tools=_tools, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        # Stop if no tool calls
        if response.stop_reason != "tool_use":
            return

        # — Mechanism 1: tool dispatch —
        results = []
        used_todo = False
        manual_compress = False

        for block in response.content:
            if block.type == "tool_use":
                if block.name == "compress":
                    manual_compress = True
                # All calls go through the async router
                output = await router.execute(
                    block.name,
                    block.input,
                    context=security_context,
                )
                logger.debug("Tool %s: %s", block.name, str(output)[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                })
                if block.name == "TodoWrite":
                    used_todo = True

        # — Mechanism 2: todo nag reminder —
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if todo.has_open_items() and rounds_without_todo >= 3:
            results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})

        messages.append({"role": "user", "content": results})

        # — Mechanism 4: manual compress —
        if manual_compress:
            logger.info("manual compact triggered")
            messages[:] = auto_compact(messages, session_id=session_id)
            return

    # If we exit the loop by exceeding MAX_LOOP_ITERATIONS
    logger.warning("agent_loop hit max iterations (%d) for session %s", MAX_LOOP_ITERATIONS, session_id)
    messages.append({
        "role": "assistant",
        "content": f"I've reached the maximum number of tool-use rounds ({MAX_LOOP_ITERATIONS}). "
                   "Please start a new conversation or break your task into smaller steps.",
    })
