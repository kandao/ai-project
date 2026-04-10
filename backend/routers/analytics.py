import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth import get_current_user
from models.session import Message, Session
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# Cost estimate constants (adjust to reflect actual LLM pricing)
COST_PER_1K_TOKENS_USD = 0.003


@router.get("/")
async def get_analytics(
    from_date: str | None = Query(default=None, alias="from", description="ISO date string"),
    to_date: str | None = Query(default=None, alias="to", description="ISO date string"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return aggregated analytics for the current user.

    Aggregates token_count and latency_ms from the messages.metadata JSONB column.
    Returns:
      - total_queries:   number of messages with role='assistant'
      - total_tokens:    sum of metadata->token_count across all qualifying messages
      - avg_latency_ms:  average of metadata->latency_ms
      - cost_estimate:   rough USD estimate based on total_tokens
    """
    # Base subquery: sessions belonging to this user
    user_session_ids = select(Session.id).where(Session.user_id == user.id)

    # Build the messages query
    stmt = select(Message).where(
        Message.session_id.in_(user_session_ids),
        Message.role == "assistant",
    )

    if from_date:
        from datetime import datetime
        try:
            dt_from = datetime.fromisoformat(from_date)
            stmt = stmt.where(Message.created_at >= dt_from)
        except ValueError:
            logger.warning("Invalid from_date: %s", from_date)

    if to_date:
        from datetime import datetime
        try:
            dt_to = datetime.fromisoformat(to_date)
            stmt = stmt.where(Message.created_at <= dt_to)
        except ValueError:
            logger.warning("Invalid to_date: %s", to_date)

    result = await db.execute(stmt)
    messages = result.scalars().all()

    total_queries = len(messages)
    total_tokens = 0
    total_latency = 0.0
    latency_count = 0

    for msg in messages:
        if msg.msg_metadata:
            token_count = msg.msg_metadata.get("token_count")
            if isinstance(token_count, (int, float)):
                total_tokens += int(token_count)

            latency_ms = msg.msg_metadata.get("latency_ms")
            if isinstance(latency_ms, (int, float)):
                total_latency += float(latency_ms)
                latency_count += 1

    avg_latency_ms = (total_latency / latency_count) if latency_count > 0 else 0.0
    cost_estimate = (total_tokens / 1000) * COST_PER_1K_TOKENS_USD

    return {
        "total_queries": total_queries,
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency_ms, 2),
        "cost_estimate": round(cost_estimate, 6),
        "cost_estimate_currency": "USD",
    }
