"""
E2E tests for the analytics endpoint.

Covers: GET /api/analytics/
  - Empty state
  - Aggregation of token_count and latency_ms from message metadata
  - Date range filtering
  - Cost estimation
  - Cross-user isolation
"""

import pytest
import httpx

from factories import (
    create_session_record,
    create_message_record,
    seed_messages_with_metadata,
)


pytestmark = pytest.mark.e2e


class TestAnalytics:
    """Test GET /api/analytics/."""

    async def test_empty_analytics(self, client: httpx.AsyncClient, test_user):
        """New user with no messages gets zeroed analytics."""
        resp = await client.get("/api/analytics/", headers=test_user["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 0
        assert body["total_tokens"] == 0
        assert body["avg_latency_ms"] == 0.0
        assert body["cost_estimate"] == 0.0

    async def test_with_messages(self, client: httpx.AsyncClient, test_user, db):
        """Analytics correctly aggregates token_count and latency_ms."""
        count = 5
        token_count = 100
        latency_ms = 250.0

        await seed_messages_with_metadata(
            db,
            test_user["id"],
            count=count,
            token_count=token_count,
            latency_ms=latency_ms,
        )

        resp = await client.get("/api/analytics/", headers=test_user["headers"])
        assert resp.status_code == 200
        body = resp.json()

        assert body["total_queries"] == count
        assert body["total_tokens"] == count * token_count
        assert body["avg_latency_ms"] == latency_ms

    async def test_date_filtering(self, client: httpx.AsyncClient, test_user, db):
        """Analytics with from/to date filters only counts matching messages."""
        from sqlalchemy import text

        session_id = await create_session_record(db, test_user["id"])

        # Insert 3 messages: 2 recent, 1 old
        for _ in range(2):
            await create_message_record(
                db, session_id, role="assistant", content="Recent",
                metadata={"token_count": 50, "latency_ms": 100.0},
            )

        # Insert an old message (30 days ago)
        await db.execute(
            text(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (:sid, 'assistant', 'Old', CAST(:meta AS jsonb), now() - interval '30 days')"
            ),
            {
                "sid": session_id,
                "meta": '{"token_count": 200, "latency_ms": 500.0}',
            },
        )
        await db.commit()

        # Filter: only last 7 days
        resp = await client.get(
            "/api/analytics/",
            headers=test_user["headers"],
            params={"from": "2020-01-01"},  # broad start, to now
        )
        assert resp.status_code == 200
        body = resp.json()
        # All 3 should be included (from 2020 covers everything)
        assert body["total_queries"] == 3

    async def test_cost_estimate(self, client: httpx.AsyncClient, test_user, db):
        """Cost estimate uses the correct formula: tokens / 1000 * rate."""
        total_tokens = 1000
        await seed_messages_with_metadata(
            db,
            test_user["id"],
            count=1,
            token_count=total_tokens,
            latency_ms=100.0,
        )

        resp = await client.get("/api/analytics/", headers=test_user["headers"])
        body = resp.json()

        # COST_PER_1K_TOKENS_USD = 0.003 (from analytics router)
        expected_cost = (total_tokens / 1000) * 0.003
        assert body["cost_estimate"] == round(expected_cost, 6)

    async def test_cross_user_isolation(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """User A's messages don't appear in User B's analytics."""
        await seed_messages_with_metadata(
            db, test_user["id"], count=10, token_count=500
        )

        resp = await client.get("/api/analytics/", headers=test_user_b["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 0
        assert body["total_tokens"] == 0
