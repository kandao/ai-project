import asyncio
import logging
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth import get_current_user
from middleware.rate_limit import get_rate_limiter, RateLimiter
from models.user import User
from services import session as session_service
from services.kafka_producer import kafka_producer
from services.token_manager import token_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Redis client — injected at startup via set_redis_client()
_redis_client: aioredis.Redis | None = None

SSE_TIMEOUT_SECONDS = 120  # Close stream if no message arrives within 2 minutes


def set_redis_client(client: aioredis.Redis) -> None:
    """Called from main.py lifespan to inject the shared Redis client."""
    global _redis_client
    _redis_client = client


def get_redis() -> aioredis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return _redis_client


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


async def _redis_sse_stream(session_id: str):
    """
    Subscribe to Redis pub/sub channel session:{session_id} and yield SSE events.

    Message protocol from agent:
      "chunk:<text>"  → SSE data event with the chunk text
      "error:<msg>"   → SSE error event
      "[DONE]"        → SSE done event, then close stream

    If no message arrives within SSE_TIMEOUT_SECONDS, closes the stream with
    a timeout error event.
    """
    redis = get_redis()
    pubsub = redis.pubsub()
    channel = f"session:{session_id}"
    await pubsub.subscribe(channel)
    logger.debug("SSE: subscribed to Redis channel=%s", channel)

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=SSE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                yield "event: error\ndata: Stream timed out waiting for agent response\n\n"
                break

            if message is None:
                # No message yet — yield a keepalive comment and loop
                yield ": keepalive\n\n"
                await asyncio.sleep(0.1)
                continue

            if message["type"] != "message":
                continue

            data: str = (
                message["data"].decode("utf-8")
                if isinstance(message["data"], bytes)
                else message["data"]
            )

            if data.startswith("chunk:"):
                chunk_text = data[len("chunk:"):]
                yield f"data: {chunk_text}\n\n"

            elif data.startswith("error:"):
                error_msg = data[len("error:"):]
                yield f"event: error\ndata: {error_msg}\n\n"
                break

            elif data == "[DONE]":
                yield "event: done\ndata: [DONE]\n\n"
                break

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        logger.debug("SSE: unsubscribed from Redis channel=%s", channel)


@router.post("")
async def chat(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> StreamingResponse:
    """
    Accept a chat message and return a streaming SSE response.

    Steps:
    1. Enforce rate limit
    2. Resolve or create the chat session
    3. Store the user message in history
    4. Generate a one-time token mapped to the user's DB role
    5. Publish to Kafka: chat.query
    6. Return StreamingResponse backed by Redis pub/sub
    """
    await rate_limiter.check(str(user.id))

    # Resolve session
    if request.session_id:
        session = await session_service.get_session(request.session_id, db)
        if session is None or session.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )
        session_id = str(session.id)
    else:
        session = await session_service.create_session(str(user.id), db)
        session_id = str(session.id)

    # Persist the user's message
    await session_service.add_message(
        session_id=session_id,
        role="user",
        content=request.message,
        db=db,
    )

    # Generate one-time token for the agent to exchange for DB credentials
    token = await token_manager.create(str(user.id), db)

    # Publish the query to the agent via Kafka
    try:
        await kafka_producer.send(
            "chat.query",
            {
                "session_id": session_id,
                "user_id": str(user.id),
                "message": request.message,
                "token": token,
            },
        )
    except Exception as exc:
        logger.error("Failed to publish chat.query for session_id=%s: %s", session_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to submit query to the processing pipeline",
        )

    return StreamingResponse(
        _redis_sse_stream(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/history")
async def get_chat_history(
    session_id: str = Query(..., description="Session ID to fetch history for"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return message history for a chat session."""
    session = await session_service.get_session(session_id, db)
    if session is None or session.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    messages = await session_service.get_messages(session_id, db, limit=limit)

    return {
        "session_id": session_id,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "metadata": msg.msg_metadata,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
    }


@router.get("/sessions")
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return all chat sessions for the current user."""
    sessions = await session_service.list_sessions(str(user.id), db)

    return {
        "sessions": [
            {
                "session_id": str(s.id),
                "title": s.title,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in sessions
        ]
    }
