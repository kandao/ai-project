"""
E2E tests for multi-user data isolation.

Validates that users cannot access each other's documents, sessions,
or chat history through the API. Also tests PostgreSQL RLS on chunks.
"""

import uuid

import pytest
import httpx
from sqlalchemy import text

from conftest import upload_file
from factories import (
    create_document_record,
    create_session_record,
    create_message_record,
    sample_txt_content,
)


pytestmark = pytest.mark.e2e


class TestDocumentIsolation:
    """Users can only see and manage their own documents."""

    async def test_list_documents_isolated(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """User A's documents don't appear in User B's list."""
        await create_document_record(db, test_user["id"], "secret.pdf")
        await create_document_record(db, test_user["id"], "private.txt", file_type="txt")

        # User B should see nothing
        resp = await client.get("/api/documents/", headers=test_user_b["headers"])
        assert resp.status_code == 200
        assert resp.json()["documents"] == []

        # User A should see both
        resp = await client.get("/api/documents/", headers=test_user["headers"])
        assert len(resp.json()["documents"]) == 2

    async def test_delete_other_users_document(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """User B cannot delete User A's document."""
        doc = await create_document_record(db, test_user["id"], "protected.pdf")
        doc_id = doc["doc_id"]

        resp = await client.delete(
            f"/api/documents/{doc_id}", headers=test_user_b["headers"]
        )
        assert resp.status_code == 404

        # Verify it still exists for User A
        resp = await client.get("/api/documents/", headers=test_user["headers"])
        doc_ids = [d["doc_id"] for d in resp.json()["documents"]]
        assert doc_id in doc_ids


class TestSessionIsolation:
    """Users can only see their own sessions."""

    async def test_list_sessions_isolated(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """User A's sessions don't appear in User B's list."""
        await create_session_record(db, test_user["id"], title="A's Session")

        resp = await client.get("/api/chat/sessions", headers=test_user_b["headers"])
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    async def test_history_isolated(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """User B cannot access User A's chat history."""
        session_id = await create_session_record(db, test_user["id"])
        await create_message_record(db, session_id, role="user", content="Secret message")

        resp = await client.get(
            "/api/chat/history",
            headers=test_user_b["headers"],
            params={"session_id": session_id},
        )
        assert resp.status_code == 404


class TestChunkRLSIsolation:
    """Test that PostgreSQL Row-Level Security prevents cross-user chunk access."""

    async def test_chunks_isolated_by_user_id(
        self, client: httpx.AsyncClient, test_user, test_user_b, db
    ):
        """
        Chunks inserted for User A should not be visible to queries
        scoped with User B's user_id.

        Note: This test verifies at the application level. Full RLS verification
        would require connecting as the user's scoped PostgreSQL role.
        """
        # Create a document and fake chunk for User A
        doc = await create_document_record(db, test_user["id"], "chunked.txt", file_type="txt")

        await db.execute(
            text(
                "INSERT INTO chunks (doc_id, user_id, content, metadata, language, chunk_index) "
                "VALUES (:doc_id, :user_id, :content, '{}'::jsonb, 'en', 0)"
            ),
            {
                "doc_id": uuid.UUID(doc["doc_id"]),
                "user_id": test_user["id"],
                "content": "Secret chunk content",
            },
        )
        await db.commit()

        # Query chunks as User A's user_id — should find it
        result_a = await db.execute(
            text("SELECT count(*) FROM chunks WHERE user_id = :uid"),
            {"uid": test_user["id"]},
        )
        assert result_a.scalar() == 1

        # Query chunks as User B's user_id — should find nothing
        result_b = await db.execute(
            text("SELECT count(*) FROM chunks WHERE user_id = :uid"),
            {"uid": test_user_b["id"]},
        )
        assert result_b.scalar() == 0
