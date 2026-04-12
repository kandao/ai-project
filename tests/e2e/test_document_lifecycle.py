"""
E2E tests for the document lifecycle:
  Upload → Worker ingestion → Status ready → List → Delete

Covers: POST/GET/DELETE /api/documents/
"""

import uuid

import pytest
import httpx

from conftest import auth_header, upload_file, wait_for_document_status
from factories import (
    sample_pdf_bytes,
    sample_docx_bytes,
    sample_txt_content,
    sample_md_content,
    oversized_content,
    create_document_record,
)


pytestmark = pytest.mark.e2e


# ── Upload: valid file types ──────────────────────────────────────────────


class TestUpload:
    """Test POST /api/documents/ with various file types and edge cases."""

    async def test_upload_pdf(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("report.pdf", sample_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "processing"
        assert body["file_type"] == "pdf"
        assert body["filename"] == "report.pdf"
        assert "doc_id" in body

    async def test_upload_docx(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("doc.docx", sample_docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert resp.status_code == 201
        assert resp.json()["file_type"] == "docx"

    async def test_upload_txt(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("notes.txt", sample_txt_content(), "text/plain")},
        )
        assert resp.status_code == 201
        assert resp.json()["file_type"] == "txt"

    async def test_upload_md(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("readme.md", sample_md_content(), "text/markdown")},
        )
        assert resp.status_code == 201
        assert resp.json()["file_type"] == "md"

    async def test_reject_unsupported_type(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert resp.status_code == 422
        assert "Unsupported file type" in resp.json()["detail"]

    async def test_reject_empty_file(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert resp.status_code == 422
        assert "empty" in resp.json()["detail"].lower()

    async def test_reject_oversized_file(self, client: httpx.AsyncClient, test_user):
        resp = await client.post(
            "/api/documents/",
            headers=test_user["headers"],
            files={"file": ("huge.txt", oversized_content(), "text/plain")},
        )
        assert resp.status_code == 413
        assert "exceeds" in resp.json()["detail"].lower()


# ── Ingestion flow ────────────────────────────────────────────────────────


class TestIngestion:
    """Test that uploaded documents are processed by the worker."""

    @pytest.mark.slow
    async def test_ingestion_completes(self, client: httpx.AsyncClient, test_user):
        """Upload a TXT file and wait for it to reach 'ready' status."""
        data = await upload_file(
            client, test_user["headers"], "sample.txt", sample_txt_content()
        )
        doc_id = data["doc_id"]
        assert data["status"] == "processing"

        doc = await wait_for_document_status(
            client, doc_id, test_user["headers"], "ready", timeout=60.0
        )
        assert doc["status"] == "ready"

    @pytest.mark.slow
    async def test_chunks_created_after_ingestion(
        self, client: httpx.AsyncClient, test_user, db
    ):
        """After ingestion, verify chunks exist in the database."""
        from sqlalchemy import text as sql_text

        data = await upload_file(
            client, test_user["headers"], "chunked.txt", sample_txt_content()
        )
        doc_id = data["doc_id"]

        await wait_for_document_status(
            client, doc_id, test_user["headers"], "ready", timeout=60.0
        )

        result = await db.execute(
            sql_text("SELECT count(*) FROM chunks WHERE doc_id = :doc_id"),
            {"doc_id": uuid.UUID(doc_id)},
        )
        count = result.scalar()
        assert count > 0, f"Expected chunks for doc {doc_id}, found 0"


# ── List documents ────────────────────────────────────────────────────────


class TestListDocuments:
    """Test GET /api/documents/ listing and pagination."""

    async def test_list_empty(self, client: httpx.AsyncClient, test_user):
        resp = await client.get("/api/documents/", headers=test_user["headers"])
        assert resp.status_code == 200
        assert resp.json()["documents"] == []

    async def test_list_returns_uploaded_docs(self, client: httpx.AsyncClient, test_user, db):
        await create_document_record(db, test_user["id"], "a.pdf")
        await create_document_record(db, test_user["id"], "b.txt", file_type="txt")

        resp = await client.get("/api/documents/", headers=test_user["headers"])
        assert resp.status_code == 200
        docs = resp.json()["documents"]
        assert len(docs) == 2

    async def test_list_pagination(self, client: httpx.AsyncClient, test_user, db):
        # Seed 25 documents
        for i in range(25):
            await create_document_record(db, test_user["id"], f"doc_{i:02d}.txt", file_type="txt")

        # Page 1: default per_page=20
        resp1 = await client.get("/api/documents/", headers=test_user["headers"])
        assert len(resp1.json()["documents"]) == 20

        # Page 2 with per_page=10
        resp2 = await client.get(
            "/api/documents/", headers=test_user["headers"],
            params={"page": 2, "per_page": 10},
        )
        assert len(resp2.json()["documents"]) == 10

        # Page 3 with per_page=10 — only 5 remaining
        resp3 = await client.get(
            "/api/documents/", headers=test_user["headers"],
            params={"page": 3, "per_page": 10},
        )
        assert len(resp3.json()["documents"]) == 5


# ── Delete documents ──────────────────────────────────────────────────────


class TestDeleteDocument:
    """Test DELETE /api/documents/{id}."""

    async def test_delete_document(self, client: httpx.AsyncClient, test_user, db):
        doc = await create_document_record(db, test_user["id"], "deleteme.pdf")
        doc_id = doc["doc_id"]

        resp = await client.delete(
            f"/api/documents/{doc_id}", headers=test_user["headers"]
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone from the list
        list_resp = await client.get("/api/documents/", headers=test_user["headers"])
        doc_ids = [d["doc_id"] for d in list_resp.json()["documents"]]
        assert doc_id not in doc_ids

    @pytest.mark.slow
    async def test_delete_cleans_chunks(self, client: httpx.AsyncClient, test_user, db):
        """After deleting a document, its chunks should be removed."""
        from sqlalchemy import text as sql_text

        data = await upload_file(
            client, test_user["headers"], "todelete.txt", sample_txt_content()
        )
        doc_id = data["doc_id"]

        await wait_for_document_status(
            client, doc_id, test_user["headers"], "ready", timeout=60.0
        )

        # Delete the document
        resp = await client.delete(
            f"/api/documents/{doc_id}", headers=test_user["headers"]
        )
        assert resp.status_code == 200

        # Wait briefly for worker to process doc.delete
        import asyncio
        await asyncio.sleep(2.0)

        result = await db.execute(
            sql_text("SELECT count(*) FROM chunks WHERE doc_id = :doc_id"),
            {"doc_id": uuid.UUID(doc_id)},
        )
        assert result.scalar() == 0

    async def test_delete_nonexistent(self, client: httpx.AsyncClient, test_user):
        fake_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/documents/{fake_id}", headers=test_user["headers"]
        )
        assert resp.status_code == 404

    async def test_delete_invalid_uuid(self, client: httpx.AsyncClient, test_user):
        resp = await client.delete(
            "/api/documents/not-a-uuid", headers=test_user["headers"]
        )
        assert resp.status_code == 422
