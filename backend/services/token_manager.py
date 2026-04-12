import logging
import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.token import Token
from models.user import User

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "otk_"
TOKEN_EXPIRY_SECONDS = 300  # 5 minutes


class TokenManager:
    """
    Manages one-time tokens used to securely pass per-user DB credentials
    from the backend to the agent without exposing them in Kafka messages.
    """

    async def create(self, user_id: str, db: AsyncSession) -> str:
        """
        Generate a new one-time token for *user_id* and persist it.
        Returns the token string (e.g. "otk_abc123...").
        """
        token_str = TOKEN_PREFIX + secrets.token_urlsafe(32)

        token = Token(
            id=uuid.uuid4(),
            token=token_str,
            user_id=uuid.UUID(user_id),
        )
        db.add(token)
        await db.commit()

        logger.debug("Created one-time token for user_id=%s", user_id)
        return token_str

    async def exchange(self, token: str, db: AsyncSession) -> dict:
        """
        Atomically consume *token* and return the user's scoped DB credentials.

        Uses a raw UPDATE...RETURNING so the operation is atomic — no race
        condition between check and update.

        Raises HTTP 401 if the token is invalid, already consumed, or expired.
        """
        result = await db.execute(
            text(
                """
                UPDATE tokens
                SET consumed_at = now()
                WHERE token = :token
                  AND consumed_at IS NULL
                  AND created_at > now() - make_interval(secs => :expiry)
                RETURNING user_id
                """
            ),
            {"token": token, "expiry": TOKEN_EXPIRY_SECONDS},
        )
        await db.commit()

        row = result.fetchone()
        if row is None:
            logger.warning("Token exchange failed: token=%s", token)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is invalid, expired, or already consumed",
            )

        user_id = str(row.user_id)
        logger.debug("Token exchanged for user_id=%s", user_id)
        return await self.get_user_db_credentials(user_id, db)

    async def get_user_db_credentials(self, user_id: str, db: AsyncSession) -> dict:
        """
        Return the scoped PostgreSQL credentials for *user_id*.

        Fetches the db_role from the users table, resets the role password via
        DBRoleManager (create_role is idempotent and returns the new password),
        and builds the credential dict.
        """
        from sqlalchemy import select
        from services.db_role_manager import db_role_manager

        result = await db.execute(
            select(User.db_role).where(User.id == uuid.UUID(user_id))
        )
        row = result.fetchone()

        if row is None or row.db_role is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User or associated database role not found",
            )

        db_role = row.db_role

        # Reset (or create) the PostgreSQL role and get the fresh password.
        # create_role() is idempotent: it creates the role if missing, or
        # resets its password if it already exists.
        role_creds = await db_role_manager.create_role(user_id)

        # Parse connection details from DATABASE_URL
        # DATABASE_URL format: postgresql+asyncpg://user:pass@host:port/dbname
        from urllib.parse import urlparse
        parsed = urlparse(settings.DATABASE_URL.replace("+asyncpg", ""))

        return {
            "db_user": db_role,
            "db_password": role_creds["db_password"],
            "db_host": parsed.hostname or "postgres",
            "db_port": parsed.port or 5432,
            "db_name": parsed.path.lstrip("/") if parsed.path else "docqa",
        }


# Module-level singleton
token_manager = TokenManager()
