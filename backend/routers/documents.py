import uuid
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from middleware.auth import get_current_user
from middleware.rate_limit import get_rate_limiter, RateLimiter
from models.document import Document
from models.user import User
from services.file_storage import file_storage
from services.kafka_producer import kafka_producer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}


def _get_file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.post("/", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    """
    Upload a document (PDF, DOCX, TXT, MD).

    Validates file type and size, saves the file, inserts a document record,
    and publishes an ingestion job to Kafka.
    """
    await rate_limiter.check(str(user.id))

    # Validate file type
    filename = file.filename or "upload"
    extension = _get_file_extension(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type '{extension}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read file content to validate size (then reset for storage)
    content = await file.read()
    file_size = len(content)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    if file_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size {file_size / 1024 / 1024:.1f} MB exceeds maximum {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty",
        )

    # Reset file position so the storage layer can read it
    await file.seek(0)

    doc_id = uuid.uuid4()

    # Persist the file
    try:
        file_path = await file_storage.save(file, str(doc_id))
    except Exception as exc:
        logger.error("File storage failed for doc_id=%s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store the uploaded file",
        )

    # Insert document record
    document = Document(
        id=doc_id,
        user_id=user.id,
        filename=filename,
        file_path=file_path,
        file_type=extension,
        file_size=file_size,
        status="processing",
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # Publish ingestion job
    try:
        await kafka_producer.send(
            "doc.ingest",
            {
                "doc_id": str(doc_id),
                "user_id": str(user.id),
                "file_path": file_path,
                "file_type": extension,
                "filename": filename,
            },
        )
    except Exception as exc:
        logger.error("Kafka publish failed for doc_id=%s: %s", doc_id, exc)
        # Document is already stored — don't delete it, but warn the caller
        # The ingestion status will remain "processing" until manually retried

    return {
        "doc_id": str(document.id),
        "filename": document.filename,
        "file_type": document.file_type,
        "file_size": document.file_size,
        "status": document.status,
        "created_at": document.created_at.isoformat(),
    }


@router.get("/")
async def list_documents(
    page: int = 1,
    per_page: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    """Return a paginated list of the current user's documents."""
    await rate_limiter.check(str(user.id))
    if page < 1:
        page = 1
    if per_page < 1 or per_page > 100:
        per_page = 20

    offset = (page - 1) * per_page

    result = await db.execute(
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    documents = result.scalars().all()

    return {
        "page": page,
        "per_page": per_page,
        "documents": [
            {
                "doc_id": str(doc.id),
                "filename": doc.filename,
                "file_type": doc.file_type,
                "file_size": doc.file_size,
                "status": doc.status,
                "created_at": doc.created_at.isoformat(),
            }
            for doc in documents
        ],
    }


@router.delete("/{doc_id}", status_code=status.HTTP_200_OK)
async def delete_document(
    doc_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Delete a document by ID.

    Publishes a doc.delete Kafka event (so the worker can clean up vectors),
    deletes the document record, and removes the stored file.
    """
    try:
        doc_uuid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid document ID format",
        )

    result = await db.execute(
        select(Document).where(Document.id == doc_uuid, Document.user_id == user.id)
    )
    document = result.scalar_one_or_none()

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    file_path = document.file_path

    # Notify worker to clean up associated chunks/vectors
    try:
        await kafka_producer.send("doc.delete", {"doc_id": doc_id})
    except Exception as exc:
        logger.error("Kafka doc.delete publish failed for doc_id=%s: %s", doc_id, exc)

    # Delete the database record
    await db.delete(document)
    await db.commit()

    # Delete the stored file
    try:
        await file_storage.delete(file_path)
    except Exception as exc:
        logger.error("File deletion failed for doc_id=%s path=%s: %s", doc_id, file_path, exc)

    return {"doc_id": doc_id, "deleted": True}
