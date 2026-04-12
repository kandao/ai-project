"""
Shared fixtures for DocQA E2E tests.

Requires running infrastructure:
  docker compose -f docker-compose.yml -f docker-compose.test.yml up -d

Environment variables (with defaults matching docker-compose.test.yml):
  TEST_DATABASE_URL   postgresql+asyncpg://docqa:docqa@localhost/docqa_test
  TEST_REDIS_URL      redis://localhost:6379/1
  TEST_BACKEND_URL    http://localhost:8000
  TEST_JWT_SECRET     test-secret-key-for-e2e
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import httpx
import jwt
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://docqa:docqa@localhost/docqa_test",
)
TEST_SYNC_DATABASE_URL = TEST_DATABASE_URL.replace("+asyncpg", "")
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")
TEST_BACKEND_URL = os.getenv("TEST_BACKEND_URL", "http://localhost:8000")
TEST_JWT_SECRET = os.getenv("TEST_JWT_SECRET", "test-secret-key-for-e2e-testing-only")
TEST_JWT_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
async def db_engine():
    """Session-scoped async SQLAlchemy engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def db_session_factory(db_engine):
    """Session-scoped sessionmaker bound to the test engine."""
    return async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
async def db(db_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Function-scoped async DB session. Rolls back after each test."""
    async with db_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture(autouse=True)
async def _clean_db(db_engine):
    """Truncate all user-created tables between tests for isolation."""
    async with db_engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE messages, sessions, tokens, documents, chunks, users "
            "CASCADE"
        ))
    yield


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
async def redis_client():
    """Session-scoped Redis client. Flushed between tests via autouse fixture."""
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.ping()
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def _clean_redis(redis_client):
    """Flush the test Redis DB between tests."""
    await redis_client.flushdb()
    yield


# ---------------------------------------------------------------------------
# HTTP client fixture (points at the running backend)
# ---------------------------------------------------------------------------

@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client pointed at the test backend."""
    async with httpx.AsyncClient(
        base_url=TEST_BACKEND_URL,
        timeout=httpx.Timeout(30.0),
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# JWT / Auth helpers
# ---------------------------------------------------------------------------

def make_jwt(
    user_id: str | uuid.UUID,
    secret: str = TEST_JWT_SECRET,
    algorithm: str = TEST_JWT_ALGORITHM,
    expires_delta: timedelta | None = None,
    expired: bool = False,
) -> str:
    """Generate a signed JWT for testing."""
    now = datetime.now(timezone.utc)
    if expired:
        exp = now - timedelta(hours=1)
    elif expires_delta:
        exp = now + expires_delta
    else:
        exp = now + timedelta(hours=1)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def auth_header(token: str) -> dict[str, str]:
    """Return an Authorization header dict for the given JWT."""
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# User factory fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def test_user(db: AsyncSession) -> dict:
    """
    Insert a test user and return a dict with user info + JWT.

    Returns:
        {
            "id": UUID,
            "email": str,
            "name": str,
            "db_role": str,
            "token": str (JWT),
            "headers": dict (Authorization header),
        }
    """
    user_id = uuid.uuid4()
    db_role = f"user_{str(user_id)[:8]}"
    email = f"testuser-{user_id}@example.com"

    await db.execute(
        text(
            "INSERT INTO users (id, email, name, hashed_password, db_role) "
            "VALUES (:id, :email, :name, :hashed_password, :db_role)"
        ),
        {"id": user_id, "email": email, "name": "Test User A",
         "hashed_password": "test-hash", "db_role": db_role},
    )
    await db.commit()

    token = make_jwt(user_id)
    return {
        "id": user_id,
        "email": email,
        "name": "Test User A",
        "db_role": db_role,
        "token": token,
        "headers": auth_header(token),
    }


@pytest.fixture
async def test_user_b(db: AsyncSession) -> dict:
    """Second test user for isolation tests."""
    user_id = uuid.uuid4()
    db_role = f"user_{str(user_id)[:8]}"
    email = f"testuser-b-{user_id}@example.com"

    await db.execute(
        text(
            "INSERT INTO users (id, email, name, hashed_password, db_role) "
            "VALUES (:id, :email, :name, :hashed_password, :db_role)"
        ),
        {"id": user_id, "email": email, "name": "Test User B",
         "hashed_password": "test-hash", "db_role": db_role},
    )
    await db.commit()

    token = make_jwt(user_id)
    return {
        "id": user_id,
        "email": email,
        "name": "Test User B",
        "db_role": db_role,
        "token": token,
        "headers": auth_header(token),
    }


# ---------------------------------------------------------------------------
# SSE parsing helper
# ---------------------------------------------------------------------------

async def collect_sse_events(response: httpx.Response) -> list[dict]:
    """
    Parse an SSE response into a list of event dicts.

    Each dict has keys: "event" (str|None), "data" (str).
    """
    events = []
    current_event = None
    current_data_lines = []

    async for line in response.aiter_lines():
        line = line.rstrip("\n")

        if line.startswith("event: "):
            current_event = line[len("event: "):]
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: "):])
        elif line == "" and current_data_lines:
            events.append({
                "event": current_event,
                "data": "\n".join(current_data_lines),
            })
            current_event = None
            current_data_lines = []
        elif line.startswith(":"):
            # SSE comment / keepalive — skip
            continue

    # Flush any trailing event
    if current_data_lines:
        events.append({
            "event": current_event,
            "data": "\n".join(current_data_lines),
        })

    return events


# ---------------------------------------------------------------------------
# Polling helper
# ---------------------------------------------------------------------------

async def wait_for_document_status(
    client: httpx.AsyncClient,
    doc_id: str,
    headers: dict,
    target_status: str,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> dict:
    """
    Poll GET /api/documents/ until the document reaches *target_status*.
    Returns the matching document dict or raises TimeoutError.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get("/api/documents/", headers=headers)
        if resp.status_code == 200:
            for doc in resp.json().get("documents", []):
                if doc["doc_id"] == doc_id and doc["status"] == target_status:
                    return doc
        await asyncio.sleep(poll_interval)

    raise TimeoutError(
        f"Document {doc_id} did not reach status '{target_status}' within {timeout}s"
    )


# ---------------------------------------------------------------------------
# File upload helper
# ---------------------------------------------------------------------------

async def upload_file(
    client: httpx.AsyncClient,
    headers: dict,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    """Upload a file via POST /api/documents/ and return the response JSON."""
    resp = await client.post(
        "/api/documents/",
        headers=headers,
        files={"file": (filename, content, content_type)},
    )
    resp.raise_for_status()
    return resp.json()
