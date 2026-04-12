"""
Unit test conftest for agent tests.

Sets required environment variables so agent modules can be imported
without a running LLM, DB, Redis, or Kafka.
"""

import os
import sys

# Add agent/ to sys.path so agent modules are importable
AGENT_DIR = os.path.join(os.path.dirname(__file__), "../../../agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# Required env vars for agent modules
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://docqa:docqa@localhost/docqa_test")
os.environ.setdefault("BACKEND_INTERNAL_URL", "http://localhost:8000")
