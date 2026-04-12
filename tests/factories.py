"""
Test data factories for DocQA E2E tests.

Provides helpers to create sample files, seed database records, etc.
"""

import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Sample file content generators
# ---------------------------------------------------------------------------

def sample_pdf_bytes() -> bytes:
    """Return a minimal valid PDF (single blank page)."""
    # Minimal PDF 1.4 with one blank page
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 44>>stream\n"
        b"BT /F1 12 Tf 100 700 Td (Hello World) Tj ET\n"
        b"endstream\nendobj\n"
        b"xref\n0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000210 00000 n \n"
        b"trailer<</Size 5/Root 1 0 R>>\n"
        b"startxref\n306\n%%EOF\n"
    )


def sample_docx_bytes() -> bytes:
    """
    Return minimal valid DOCX bytes.

    DOCX is a ZIP containing XML files. This creates the bare minimum
    structure that python-docx can parse.
    """
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        # _rels/.rels
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        # word/_rels/document.xml.rels
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            "</Relationships>",
        )
        # word/document.xml
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Test document content for E2E testing.</w:t></w:r></w:p>"
            "</w:body>"
            "</w:document>",
        )
    return buf.getvalue()


def sample_txt_content() -> bytes:
    """Return sample plain text content."""
    return b"This is a sample text document for E2E testing.\n" * 10


def sample_md_content() -> bytes:
    """Return sample Markdown content."""
    return (
        b"# Test Document\n\n"
        b"This is a **markdown** document for E2E testing.\n\n"
        b"## Section 1\n\n"
        b"Some content about testing and retrieval.\n\n"
        b"## Section 2\n\n"
        b"More content about document processing.\n"
    )


def oversized_content(size_mb: int = 11) -> bytes:
    """Return content larger than MAX_UPLOAD_SIZE_MB (default 10 MB in test)."""
    return b"x" * (size_mb * 1024 * 1024)


# ---------------------------------------------------------------------------
# Database seeding helpers
# ---------------------------------------------------------------------------

async def create_document_record(
    db: AsyncSession,
    user_id: uuid.UUID,
    filename: str = "test.pdf",
    file_type: str = "pdf",
    status: str = "ready",
    file_path: str = "/data/test-uploads/fake.pdf",
    file_size: int = 1024,
) -> dict:
    """Insert a document record directly into the DB. Returns doc info dict."""
    doc_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO documents (id, user_id, filename, file_path, file_type, file_size, status) "
            "VALUES (:id, :user_id, :filename, :file_path, :file_type, :file_size, :status)"
        ),
        {
            "id": doc_id,
            "user_id": user_id,
            "filename": filename,
            "file_path": file_path,
            "file_type": file_type,
            "file_size": file_size,
            "status": status,
        },
    )
    await db.commit()
    return {"doc_id": str(doc_id), "filename": filename, "status": status}


async def create_session_record(
    db: AsyncSession,
    user_id: uuid.UUID,
    title: str | None = None,
) -> str:
    """Insert a session record and return the session_id as string."""
    session_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO sessions (id, user_id, title) "
            "VALUES (:id, :user_id, :title)"
        ),
        {"id": session_id, "user_id": user_id, "title": title},
    )
    await db.commit()
    return str(session_id)


async def create_message_record(
    db: AsyncSession,
    session_id: str,
    role: str = "assistant",
    content: str = "Test response",
    metadata: dict | None = None,
) -> int:
    """Insert a message record and return the message id."""
    result = await db.execute(
        text(
            "INSERT INTO messages (session_id, role, content, metadata) "
            "VALUES (:session_id, :role, :content, CAST(:metadata AS jsonb)) "
            "RETURNING id"
        ),
        {
            "session_id": uuid.UUID(session_id),
            "role": role,
            "content": content,
            "metadata": __import__("json").dumps(metadata) if metadata else "{}",
        },
    )
    await db.commit()
    row = result.fetchone()
    return row.id


async def seed_messages_with_metadata(
    db: AsyncSession,
    user_id: uuid.UUID,
    count: int = 5,
    token_count: int = 100,
    latency_ms: float = 250.0,
) -> str:
    """
    Create a session with *count* assistant messages, each having
    known token_count and latency_ms in metadata. Returns session_id.
    """
    session_id = await create_session_record(db, user_id, title="Analytics test")

    for i in range(count):
        await create_message_record(
            db,
            session_id,
            role="assistant",
            content=f"Response {i}",
            metadata={"token_count": token_count, "latency_ms": latency_ms},
        )

    return session_id
