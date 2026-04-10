import logging
import secrets

import asyncpg

from config import settings

logger = logging.getLogger(__name__)


class DBRoleManager:
    """
    Manages per-user PostgreSQL roles for row-level security.

    Uses asyncpg directly (not SQLAlchemy) because DDL statements like
    CREATE ROLE must run outside of a transaction block.
    """

    def _role_name(self, user_id: str) -> str:
        return f"user_{user_id[:8]}"

    async def _get_raw_connection(self) -> asyncpg.Connection:
        """
        Open a raw asyncpg connection using the configured DATABASE_URL.
        Strips the +asyncpg dialect prefix that SQLAlchemy requires.
        """
        dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        return await asyncpg.connect(dsn)

    async def create_role(self, user_id: str) -> dict:
        """
        Create a scoped PostgreSQL LOGIN role for *user_id*.

        The role is granted SELECT on the chunks and documents tables,
        plus USAGE on the public schema.  Row-level security policies in
        init.sql ensure the role can only see rows belonging to the user.

        Returns {"db_user": role_name, "db_password": password}.
        """
        role_name = self._role_name(user_id)
        password = secrets.token_urlsafe(16)

        conn = await self._get_raw_connection()
        try:
            # Check if the role already exists to make this idempotent
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = $1", role_name
            )
            if not exists:
                # DDL must run outside a transaction — use execute() directly.
                # asyncpg wraps statements in implicit transactions by default;
                # we execute each DDL individually to avoid issues.
                await conn.execute(
                    f"CREATE ROLE {role_name} LOGIN PASSWORD '{password}'"
                )
                await conn.execute(
                    f"GRANT USAGE ON SCHEMA public TO {role_name}"
                )
                await conn.execute(
                    f"GRANT SELECT ON chunks, documents TO {role_name}"
                )
                logger.info("Created PostgreSQL role: %s", role_name)
            else:
                # Role exists — reset the password so we can return a known value
                await conn.execute(
                    f"ALTER ROLE {role_name} PASSWORD '{password}'"
                )
                logger.info("Reset password for existing PostgreSQL role: %s", role_name)
        finally:
            await conn.close()

        return {"db_user": role_name, "db_password": password}

    async def drop_role(self, user_id: str) -> None:
        """
        Drop the PostgreSQL role associated with *user_id*.

        Revokes grants first so the DROP succeeds even if objects remain.
        """
        role_name = self._role_name(user_id)

        conn = await self._get_raw_connection()
        try:
            # Revoke existing privileges before dropping
            await conn.execute(
                f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role_name}"
            )
            await conn.execute(f"DROP ROLE IF EXISTS {role_name}")
            logger.info("Dropped PostgreSQL role: %s", role_name)
        except Exception as exc:
            logger.error("Failed to drop role %s: %s", role_name, exc)
            raise
        finally:
            await conn.close()


# Module-level singleton
db_role_manager = DBRoleManager()
