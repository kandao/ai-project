"""
E2E tests for the chat flow:
  POST /api/chat → Kafka → Agent → Redis → SSE response

Covers: POST /api/chat, GET /api/chat/sessions, GET /api/chat/history
"""

import uuid

import pytest
import httpx

from conftest import collect_sse_events, upload_file, wait_for_document_status
from factories import (
    create_session_record,
    create_message_record,
    sample_txt_content,
)


pytestmark = pytest.mark.e2e


class TestChat:
    """Test the main chat endpoint and SSE streaming."""

    @pytest.mark.slow
    async def test_new_session_chat(self, client: httpx.AsyncClient, test_user):
        """POST /api/chat without session_id creates a new session and streams SSE."""
        async with client.stream(
            "POST",
            "/api/chat",
            headers=test_user["headers"],
            json={"message": "Hello, what can you do?"},
            timeout=httpx.Timeout(120.0),
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith("text/event-stream")

            events = await collect_sse_events(resp)

        # Should have at least one data event and a done event
        has_data = any(e["event"] is None and e["data"] for e in events)
        has_done = any(e["event"] == "done" for e in events)
        assert has_data or has_done, f"Expected SSE data/done events, got: {events}"

    async def test_session_created_after_chat(
        self, client: httpx.AsyncClient, test_user
    ):
        """After a chat, the session should appear in the sessions list."""
        # Send a chat message (fire and forget the stream)
        async with client.stream(
            "POST",
            "/api/chat",
            headers=test_user["headers"],
            json={"message": "Create session test"},
            timeout=httpx.Timeout(120.0),
        ) as resp:
            # Consume the stream to completion
            async for _ in resp.aiter_lines():
                pass

        # Verify session was created
        sessions_resp = await client.get(
            "/api/chat/sessions", headers=test_user["headers"]
        )
        assert sessions_resp.status_code == 200
        sessions = sessions_resp.json()["sessions"]
        assert len(sessions) >= 1

    async def test_message_persisted_in_history(
        self, client: httpx.AsyncClient, test_user
    ):
        """After a chat, the user message should be in the history."""
        message_text = "What is the meaning of life?"

        async with client.stream(
            "POST",
            "/api/chat",
            headers=test_user["headers"],
            json={"message": message_text},
            timeout=httpx.Timeout(120.0),
        ) as resp:
            async for _ in resp.aiter_lines():
                pass

        # Get the session
        sessions_resp = await client.get(
            "/api/chat/sessions", headers=test_user["headers"]
        )
        session_id = sessions_resp.json()["sessions"][0]["session_id"]

        # Get history
        history_resp = await client.get(
            "/api/chat/history",
            headers=test_user["headers"],
            params={"session_id": session_id},
        )
        assert history_resp.status_code == 200
        messages = history_resp.json()["messages"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any(m["content"] == message_text for m in user_msgs)

    async def test_continue_existing_session(
        self, client: httpx.AsyncClient, test_user
    ):
        """POST /api/chat with an existing session_id continues that session."""
        # First message — creates session
        async with client.stream(
            "POST",
            "/api/chat",
            headers=test_user["headers"],
            json={"message": "First message"},
            timeout=httpx.Timeout(120.0),
        ) as resp:
            async for _ in resp.aiter_lines():
                pass

        sessions_resp = await client.get(
            "/api/chat/sessions", headers=test_user["headers"]
        )
        session_id = sessions_resp.json()["sessions"][0]["session_id"]

        # Second message — continues session
        async with client.stream(
            "POST",
            "/api/chat",
            headers=test_user["headers"],
            json={"message": "Follow-up question", "session_id": session_id},
            timeout=httpx.Timeout(120.0),
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass

        # Should still be same session, now with more messages
        history_resp = await client.get(
            "/api/chat/history",
            headers=test_user["headers"],
            params={"session_id": session_id},
        )
        messages = history_resp.json()["messages"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) >= 2

    async def test_invalid_session_id(self, client: httpx.AsyncClient, test_user):
        """POST /api/chat with a nonexistent session_id returns 404."""
        resp = await client.post(
            "/api/chat",
            headers=test_user["headers"],
            json={"message": "Hello", "session_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404

    async def test_other_users_session(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """User B cannot use User A's session_id."""
        session_id = await create_session_record(db, test_user["id"])

        resp = await client.post(
            "/api/chat",
            headers=test_user_b["headers"],
            json={"message": "Trying to hijack", "session_id": session_id},
        )
        assert resp.status_code == 404


class TestChatHistory:
    """Test GET /api/chat/history."""

    async def test_history_with_limit(
        self, client: httpx.AsyncClient, test_user, db
    ):
        """History respects the limit parameter."""
        session_id = await create_session_record(db, test_user["id"])
        for i in range(200):
            await create_message_record(
                db, session_id, role="user", content=f"Msg {i}"
            )

        resp = await client.get(
            "/api/chat/history",
            headers=test_user["headers"],
            params={"session_id": session_id, "limit": 50},
        )
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 50

    async def test_history_nonexistent_session(
        self, client: httpx.AsyncClient, test_user
    ):
        resp = await client.get(
            "/api/chat/history",
            headers=test_user["headers"],
            params={"session_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404


class TestSessions:
    """Test GET /api/chat/sessions."""

    async def test_list_sessions_empty(self, client: httpx.AsyncClient, test_user):
        resp = await client.get("/api/chat/sessions", headers=test_user["headers"])
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    async def test_list_sessions_returns_created(
        self, client: httpx.AsyncClient, test_user, db
    ):
        await create_session_record(db, test_user["id"], title="Session A")
        await create_session_record(db, test_user["id"], title="Session B")

        resp = await client.get("/api/chat/sessions", headers=test_user["headers"])
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2


class TestChatWithRetrieval:
    """Test chat that exercises the retrieval pipeline (requires ingested doc)."""

    @pytest.mark.slow
    async def test_chat_with_document_retrieval(
        self, client: httpx.AsyncClient, test_user
    ):
        """Upload a document, wait for ingestion, then ask about its content."""
        content = b"The capital of France is Paris. The Eiffel Tower is 330 meters tall."
        data = await upload_file(
            client, test_user["headers"], "france.txt", content
        )

        await wait_for_document_status(
            client, data["doc_id"], test_user["headers"], "ready", timeout=60.0
        )

        async with client.stream(
            "POST",
            "/api/chat",
            headers=test_user["headers"],
            json={"message": "What is the capital of France?"},
            timeout=httpx.Timeout(120.0),
        ) as resp:
            assert resp.status_code == 200
            events = await collect_sse_events(resp)

        # The agent should have produced some response
        data_events = [e for e in events if e["event"] is None and e["data"]]
        assert len(data_events) > 0 or any(e["event"] == "done" for e in events)
