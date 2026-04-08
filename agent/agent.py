#!/usr/bin/env python3
"""
agent.py — Agent01 entrypoint.

Modes:
  CLI:   python agent.py              (interactive REPL)
  Kafka: KAFKA_BOOTSTRAP_SERVERS=... python agent.py  (consumer mode)

REPL commands:
  /compact  — manually compress context
  /tasks    — show todo list
  q / exit  — quit
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# Strip auth token when using a custom base URL (e.g. MiniMax)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def cli_mode():
    """Interactive REPL — standalone analyst."""
    from loop import agent_loop, auto_compact, TODO

    history = []
    print("\033[1mAgent01 — AI Business Analyst\033[0m")
    print("Type a query, or /compact, /tasks, q to quit.\n")

    while True:
        try:
            query = input("\033[36magent >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        stripped = query.strip().lower()
        if stripped in ("q", "exit", ""):
            break

        if stripped == "/compact":
            if history:
                print("[manual compact via /compact]")
                history[:] = auto_compact(history)
            else:
                print("Nothing to compact.")
            continue

        if stripped == "/tasks":
            print(TODO.render())
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history)

        # Print the final assistant text
        if history:
            last = history[-1]
            content = last.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "text"):
                        print(block.text)
            elif isinstance(content, str):
                print(content)
        print()


def kafka_mode():
    """Kafka consumer mode — import and start the consumer."""
    try:
        from consumer import start_consumer
        start_consumer()
    except ImportError:
        print("Error: consumer.py not found. Kafka mode requires consumer.py.")
        sys.exit(1)


if __name__ == "__main__":
    if os.getenv("KAFKA_BOOTSTRAP_SERVERS"):
        kafka_mode()
    else:
        cli_mode()
