"""
E2E tests for error handling scenarios.

Covers:
  - Malformed request bodies
  - Missing required fields
  - Ingestion failures (corrupted files)
  - Agent error recovery
"""

import pytest
import httpx

from conftest import upload_file, wait_for_document_status


pytestmark = pytest.mark.e2e


class TestRequestValidation:
    """Test that invalid requests return appropriate error responses."""

    async def test_chat_missing_message_field(
        self, client: httpx.AsyncClient, test_user
    ):
        """POST /api/chat without 'message' returns 422."""
        resp = await client.post(
            "/api/chat",
            headers={
                **test_user["headers"],
                "Content-Type": "application/json",
            },
            content="{}",
        )
        assert resp.status_code == 422

    async def test_chat_invalid_json(self, client: httpx.AsyncClient, test_user):
        """POST /api/chat with invalid JSON returns 422."""
        resp = await client.post(
            "/api/chat",
            headers={
                **test_user["headers"],
                "Content-Type": "application/json",
            },
            content="{invalid json",
        )
        assert resp.status_code == 422

    async def test_upload_no_file(self, client: httpx.AsyncClient, test_user):
        """POST /api/documents/ without a file returns 422."""
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
        )
        assert resp.status_code == 422


class TestIngestionFailure:
    """Test that corrupted files result in 'failed' status."""

    @pytest.mark.slow
    async def test_corrupted_pdf_fails_ingestion(
        self, client: httpx.AsyncClient, test_user
    ):
        """Upload a file with .pdf extension but invalid content."""
        corrupted_pdf = b"This is not a valid PDF file at all"

        data = await upload_file(
            client, test_user["headers"], "broken.pdf", corrupted_pdf
        )
        doc_id = data["doc_id"]
        assert data["status"] == "processing"

        # The worker should mark this as 'failed' after attempting extraction
        doc = await wait_for_document_status(
            client, doc_id, test_user["headers"], "failed", timeout=30.0
        )
        assert doc["status"] == "failed"


class TestHealthEndpoint:
    """Test the health check endpoint (no auth required)."""

    async def test_health_check(self, client: httpx.AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
