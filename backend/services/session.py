import uuid
import logging
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.session import Session, Message

logger = logging.getLogger(__name__)


async def create_session(user_id: str, db: AsyncSession, title: str | None = None) -> Session:
    """Create and return a new chat session for *user_id*."""
    session = Session(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        title=title,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.debug("Created session id=%s for user_id=%s", session.id, user_id)
    return session


async def get_session(session_id: str, db: AsyncSession) -> Session | None:
    """Fetch a single session by *session_id*. Returns None if not found."""
    result = await db.execute(
        select(Session).where(Session.id == uuid.UUID(session_id))
    )
    return result.scalar_one_or_none()


async def list_sessions(user_id: str, db: AsyncSession) -> list[Session]:
    """Return all sessions for *user_id*, ordered by most recently updated."""
    result = await db.execute(
        select(Session)
        .where(Session.user_id == uuid.UUID(user_id))
        .order_by(desc(Session.updated_at))
    )
    return list(result.scalars().all())


async def add_message(
    session_id: str,
    role: str,
    content: str,
    db: AsyncSession,
    metadata: dict[str, Any] | None = None,
) -> Message:
    """
    Append a message to *session_id* and update the session's updated_at timestamp.
    Returns the persisted Message.
    """
    message = Message(
        session_id=uuid.UUID(session_id),
        role=role,
        content=content,
        msg_metadata=metadata,
    )
    db.add(message)

    # Bump the session's updated_at so list_sessions returns it at the top
    session_result = await db.execute(
        select(Session).where(Session.id == uuid.UUID(session_id))
    )
    session = session_result.scalar_one_or_none()
    if session is not None:
        from sqlalchemy import func
        session.updated_at = func.now()

    await db.commit()
    await db.refresh(message)
    logger.debug(
        "Added message id=%s to session_id=%s role=%s", message.id, session_id, role
    )
    return message


async def get_messages(
    session_id: str,
    db: AsyncSession,
    limit: int = 100,
) -> list[Message]:
    """Return the most recent *limit* messages for *session_id*, oldest first."""
    result = await db.execute(
        select(Message)
        .where(Message.session_id == uuid.UUID(session_id))
        .order_by(Message.id)
        .limit(limit)
    )
    return list(result.scalars().all())
