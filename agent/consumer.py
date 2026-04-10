"""
consumer.py — Kafka consumer for Agent02/Agent03.

Listens on KAFKA_TOPIC (default: "chat.query") for messages:
    { "message": str, "session_id": str, "token": str }

Processing pipeline:
  1. Exchange token for scoped DB credentials (Agent03 per-user access control)
  2. Build scoped tool handlers bound to the user's DB connection
  3. Run agent_loop() for all tool-use rounds (blocking chat)
  4. Stream the final text response to Redis pub/sub as token chunks

Redis channel: session:{session_id}
  - Each text chunk published as: chunk:<text>
  - On error:                      error:<message>
  - Completion sentinel:           [DONE]

Env vars:
    KAFKA_BOOTSTRAP_SERVERS  (required to activate Kafka mode)
    KAFKA_GROUP_ID           consumer group (default: "agent-group")
    KAFKA_TOPIC              topic to consume (default: "chat.query")
    REDIS_URL                Redis connection URL (default: "redis://localhost:6379")
    BACKEND_INTERNAL_URL     internal backend URL for token exchange
"""

import asyncio
import json
import logging
import os
import signal
import sys

from auth import AuthError, build_db_url, exchange_token
from loop import SYSTEM, agent_loop
from security.scanner import scan_user_message
from security.audit import AuditLogger

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Scoped tool builder ────────────────────────────────────────────────

def build_scoped_tools(db_url: str):
    """
    Build a tools list and handler dict scoped to a specific user's database URL.

    The scoped handlers override hybrid_retrieval and query_database so they
    connect to the user's database.  The LLM never sees db_url.
    """
    from tools import TOOLS as ALL_TOOLS, TOOL_HANDLERS as ALL_HANDLERS
    from tools.retrieval import hybrid_retrieval
    from tools.database import query_database

    scoped_handlers = {
        **ALL_HANDLERS,
        "hybrid_retrieval": lambda **kw: hybrid_retrieval(
            kw["query"], kw.get("top_k", 5), db_url=db_url
        ),
        "query_database": lambda **kw: query_database(kw["sql"], db_url=db_url),
    }
    return ALL_TOOLS, scoped_handlers


# ── Redis helpers ──────────────────────────────────────────────────────

def _get_redis():
    """Create and return a Redis client."""
    import redis
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    return redis.from_url(url)


def _publish(r, channel: str, message: str):
    try:
        r.publish(channel, message)
    except Exception as e:
        logger.warning("Redis publish error on %s: %s", channel, e)


# ── Message processing ─────────────────────────────────────────────────

def _extract_final_text(messages: list) -> str:
    """
    Extract the final assistant text from the message history.
    Handles both Anthropic SDK content block objects and plain dicts.
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            text = "".join(parts)
            if text:
                return text
    return ""


def process_message(payload: dict, r) -> None:
    """
    Process a single Kafka message payload end-to-end.

    1. Extract token, session_id, message text
    2. Scan user message for injection attempts
    3. Exchange token for scoped DB credentials
    4. Run agent_loop (tool rounds use blocking chat())
    5. Stream final response text to Redis in chunks
    """
    session_id = payload.get("session_id", "unknown")
    channel = f"session:{session_id}"
    message_text = payload.get("message", "")
    audit = AuditLogger()

    # — Agent04: Scan user message for injection —
    injection_matches = scan_user_message(message_text)
    if injection_matches:
        asyncio.run(
            audit.log_injection("user_message", injection_matches, {"session_id": session_id})
        )
        # Don't block — but disable dangerous tools for this session
        # The policy engine in kafka mode already blocks bash/write

    # — Auth: exchange token for scoped DB credentials —
    token = payload.get("token")
    if not token:
        _publish(r, channel, "error:Missing authentication token")
        _publish(r, channel, "[DONE]")
        return

    try:
        creds = exchange_token(token)
        db_url = build_db_url(creds)
    except AuthError as e:
        _publish(r, channel, f"error:Authentication failed: {e}")
        _publish(r, channel, "[DONE]")
        return

    # — Build scoped tools —
    tools, handlers = build_scoped_tools(db_url)

    # — Build initial message history —
    messages = [{"role": "user", "content": message_text}]

    # — Agent04: Build security context for this session —
    security_context = {
        "mode": "kafka",
        "session_id": session_id,
        "user_id": payload.get("user_id"),
        "injection_detected": bool(injection_matches),
    }

    # — Run ReAct loop (tool-use rounds use blocking chat, final response included) —
    try:
        asyncio.run(
            agent_loop(messages, tools=tools, handlers=handlers, security_context=security_context)
        )
    except Exception as e:
        logger.exception("agent_loop error for session %s", session_id)
        _publish(r, channel, f"error:Agent error: {e}")
        _publish(r, channel, "[DONE]")
        return

    # — Stream final response text to Redis —
    # agent_loop has completed and appended the final assistant message.
    # Extract the text and publish it in chunks to Redis for the SSE relay.
    final_text = _extract_final_text(messages)
    if final_text:
        # Stream in ~50-char chunks to simulate token-level granularity
        chunk_size = 50
        for i in range(0, len(final_text), chunk_size):
            _publish(r, channel, f"chunk:{final_text[i:i + chunk_size]}")

    _publish(r, channel, "[DONE]")


# ── Consumer loop ──────────────────────────────────────────────────────

def start_consumer():
    """
    Start the Kafka consumer loop.

    Handles SIGTERM for graceful shutdown.
    """
    from kafka import KafkaConsumer

    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    group_id = os.getenv("KAFKA_GROUP_ID", "agent-group")
    topic = os.getenv("KAFKA_TOPIC", "chat.query")

    logger.info("Starting Kafka consumer: topic=%s group=%s servers=%s",
                topic, group_id, bootstrap_servers)

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers.split(","),
        group_id=group_id,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )

    r = _get_redis()

    # Graceful shutdown on SIGTERM
    _running = [True]

    def _handle_sigterm(signum, frame):
        logger.info("SIGTERM received, shutting down consumer.")
        _running[0] = False
        consumer.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    logger.info("Consumer ready, waiting for messages...")
    try:
        for kafka_message in consumer:
            if not _running[0]:
                break
            payload = kafka_message.value
            if not isinstance(payload, dict):
                logger.warning("Skipping non-dict message: %r", payload)
                continue
            logger.info("Processing message session_id=%s", payload.get("session_id"))
            try:
                process_message(payload, r)
            except Exception:
                logger.exception("Unhandled error processing message %r", payload)
    except Exception:
        logger.exception("Consumer loop error")
    finally:
        consumer.close()
        logger.info("Consumer stopped.")
