"""
E2E tests for Redis-based sliding window rate limiting.

The test docker-compose sets RATE_LIMIT_REQUESTS_PER_MINUTE=30.
Rate limiting is applied per-user on endpoints that call rate_limiter.check().

We test against GET /api/documents/ since it's lightweight and rate-limited.
"""

import asyncio

import pytest
import httpx

from conftest import auth_header


pytestmark = pytest.mark.e2e

# Must match docker-compose.test.yml RATE_LIMIT_REQUESTS_PER_MINUTE
RATE_LIMIT = 30


class TestRateLimiting:
    """Test sliding window rate limiter enforcement."""

    async def test_under_limit(self, client: httpx.AsyncClient, test_user):
        """Requests well under the limit all succeed."""
        for _ in range(5):
            resp = await client.get("/api/documents/", headers=test_user["headers"])
            assert resp.status_code == 200

    async def test_at_limit(self, client: httpx.AsyncClient, test_user):
        """Exactly RATE_LIMIT requests within the window all succeed."""
        results = []
        for _ in range(RATE_LIMIT):
            resp = await client.get("/api/documents/", headers=test_user["headers"])
            results.append(resp.status_code)

        assert all(s == 200 for s in results), (
            f"Expected all 200s at limit, got: {results}"
        )

    async def test_over_limit(self, client: httpx.AsyncClient, test_user):
        """The (RATE_LIMIT + 1)th request returns 429."""
        # Exhaust the limit
        for _ in range(RATE_LIMIT):
            await client.get("/api/documents/", headers=test_user["headers"])

        # Next request should be rejected
        resp = await client.get("/api/documents/", headers=test_user["headers"])
        assert resp.status_code == 429
        assert "Rate limit exceeded" in resp.json()["detail"]
        assert "Retry-After" in resp.headers

    async def test_per_user_isolation(
        self, client: httpx.AsyncClient, test_user, test_user_b
    ):
        """User A hitting the limit does not affect User B."""
        # Exhaust User A's limit
        for _ in range(RATE_LIMIT + 1):
            await client.get("/api/documents/", headers=test_user["headers"])

        # User B should still succeed
        resp = await client.get("/api/documents/", headers=test_user_b["headers"])
        assert resp.status_code == 200

    @pytest.mark.slow
    async def test_window_reset(self, client: httpx.AsyncClient, test_user, redis_client):
        """After the rate limit window resets, requests succeed again."""
        # Exhaust the limit
        for _ in range(RATE_LIMIT + 1):
            await client.get("/api/documents/", headers=test_user["headers"])

        # Verify we're rate limited
        resp = await client.get("/api/documents/", headers=test_user["headers"])
        assert resp.status_code == 429

        # Clear the rate limit key in Redis to simulate window expiry
        user_id = str(test_user["id"])
        await redis_client.delete(f"ratelimit:{user_id}")

        # Should succeed again
        resp = await client.get("/api/documents/", headers=test_user["headers"])
        assert resp.status_code == 200
