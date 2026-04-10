import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.token_manager import token_manager

logger = logging.getLogger(__name__)

# NOTE: This router intentionally has NO get_current_user dependency.
# It is protected exclusively by Docker network isolation — only the agent
# service (running inside the Docker network) can reach /api/internal/*.
router = APIRouter(prefix="/api/internal", tags=["internal"])


class TokenExchangeRequest(BaseModel):
    token: str


@router.post("/token-exchange")
async def exchange_token(
    body: TokenExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Exchange a one-time token for scoped PostgreSQL credentials.

    Called exclusively by the agent immediately after it receives a
    chat.query Kafka message.  The token is atomically consumed on first
    use — any subsequent call with the same token returns HTTP 401.

    Returns:
        {
            "db_user":     "<pg_role_name>",
            "db_password": "<role_password>",
            "db_host":     "<postgres_host>",
            "db_port":     5432,
            "db_name":     "<database_name>"
        }
    """
    logger.info("Token exchange requested (token prefix=%s)", body.token[:8])
    credentials = await token_manager.exchange(body.token, db)
    logger.info("Token exchange successful")
    return credentials
