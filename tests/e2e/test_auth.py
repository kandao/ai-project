"""
E2E tests for authentication and token management.

Covers:
  - JWT validation on protected endpoints
  - One-time token exchange (POST /api/internal/token-exchange)
  - Token replay and expiry
"""

import uuid
from datetime import timedelta

import pytest
import httpx
from sqlalchemy import text

from conftest import make_jwt, auth_header, TEST_JWT_SECRET


pytestmark = pytest.mark.e2e


class TestJWTAuth:
    """Test JWT Bearer token authentication on protected endpoints."""

    async def test_no_token(self, client: httpx.AsyncClient):
        """Request without Authorization header returns 401 or 403."""
        resp = await client.get("/api/documents/")
        assert resp.status_code in (401, 403)

    async def test_invalid_jwt(self, client: httpx.AsyncClient):
        """Malformed JWT returns 401."""
        resp = await client.get(
            "/api/documents/",
            headers=auth_header("this-is-not-a-jwt"),
        )
        assert resp.status_code in (401, 403)

    async def test_expired_jwt(self, client: httpx.AsyncClient, test_user):
        """Expired JWT returns 401."""
        expired_token = make_jwt(test_user["id"], expired=True)
        resp = await client.get(
            "/api/documents/",
            headers=auth_header(expired_token),
        )
        assert resp.status_code == 401

    async def test_wrong_secret(self, client: httpx.AsyncClient, test_user):
        """JWT signed with wrong secret returns 401."""
        bad_token = make_jwt(test_user["id"], secret="wrong-secret-key")
        resp = await client.get(
            "/api/documents/",
            headers=auth_header(bad_token),
        )
        assert resp.status_code == 401

    async def test_nonexistent_user(self, client: httpx.AsyncClient):
        """Valid JWT but user_id not in database returns 401."""
        fake_user_id = uuid.uuid4()
        token = make_jwt(fake_user_id)
        resp = await client.get(
            "/api/documents/",
            headers=auth_header(token),
        )
        assert resp.status_code == 401
        assert "not found" in resp.json()["detail"].lower()

    async def test_valid_token(self, client: httpx.AsyncClient, test_user):
        """Valid JWT allows access to protected endpoint."""
        resp = await client.get(
            "/api/documents/",
            headers=test_user["headers"],
        )
        assert resp.status_code == 200


class TestTokenExchange:
    """Test POST /api/internal/token-exchange (one-time token system)."""

    async def test_exchange_valid_token(self, client: httpx.AsyncClient, test_user, db):
        """Exchange a valid token and receive DB credentials."""
        import secrets

        token_str = f"otk_{secrets.token_urlsafe(32)}"
        await db.execute(
            text(
                "INSERT INTO tokens (id, token, user_id) "
                "VALUES (:id, :token, :user_id)"
            ),
            {"id": uuid.uuid4(), "token": token_str, "user_id": test_user["id"]},
        )
        await db.commit()

        resp = await client.post(
            "/api/internal/token-exchange",
            json={"token": token_str},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "db_user" in body
        assert "db_password" in body
        assert "db_host" in body
        assert "db_port" in body
        assert "db_name" in body

    async def test_token_replay_rejected(
        self, client: httpx.AsyncClient, test_user, db
    ):
        """A token can only be exchanged once — second attempt fails."""
        import secrets

        token_str = f"otk_{secrets.token_urlsafe(32)}"
        await db.execute(
            text(
                "INSERT INTO tokens (id, token, user_id) "
                "VALUES (:id, :token, :user_id)"
            ),
            {"id": uuid.uuid4(), "token": token_str, "user_id": test_user["id"]},
        )
        await db.commit()

        # First exchange — should succeed
        resp1 = await client.post(
            "/api/internal/token-exchange",
            json={"token": token_str},
        )
        assert resp1.status_code == 200

        # Second exchange — should fail
        resp2 = await client.post(
            "/api/internal/token-exchange",
            json={"token": token_str},
        )
        assert resp2.status_code == 401

    async def test_token_expiry(self, client: httpx.AsyncClient, test_user, db):
        """A token created more than 5 minutes ago should be rejected."""
        import secrets

        token_str = f"otk_{secrets.token_urlsafe(32)}"
        # Insert with created_at 10 minutes in the past
        await db.execute(
            text(
                "INSERT INTO tokens (id, token, user_id, created_at) "
                "VALUES (:id, :token, :user_id, now() - interval '10 minutes')"
            ),
            {"id": uuid.uuid4(), "token": token_str, "user_id": test_user["id"]},
        )
        await db.commit()

        resp = await client.post(
            "/api/internal/token-exchange",
            json={"token": token_str},
        )
        assert resp.status_code == 401

    async def test_exchange_invalid_token(self, client: httpx.AsyncClient):
        """Exchanging a non-existent token returns 401."""
        resp = await client.post(
            "/api/internal/token-exchange",
            json={"token": "otk_nonexistent_token_value"},
        )
        assert resp.status_code == 401
