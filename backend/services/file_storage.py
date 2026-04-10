import logging
from pathlib import Path

import aiofiles
from fastapi import UploadFile

from config import settings

logger = logging.getLogger(__name__)


class FileStorage:
    """
    File storage abstraction supporting local filesystem and Amazon S3.

    The active backend is determined by settings.STORAGE_BACKEND:
      - "local": saves to STORAGE_LOCAL_PATH/{doc_id}/{filename}
      - "s3":    uploads to S3_BUCKET under key {doc_id}/{filename}
    """

    async def save(self, file: UploadFile, doc_id: str) -> str:
        """
        Persist *file* and return the canonical file_path string.

        For local storage, file_path is the absolute filesystem path.
        For S3, file_path is the S3 object key (e.g. "{doc_id}/{filename}").
        """
        if settings.STORAGE_BACKEND == "s3":
            return await self._save_s3(file, doc_id)
        return await self._save_local(file, doc_id)

    async def delete(self, file_path: str) -> None:
        """Delete a previously saved file."""
        if settings.STORAGE_BACKEND == "s3":
            await self._delete_s3(file_path)
        else:
            await self._delete_local(file_path)

    # ------------------------------------------------------------------
    # Local storage
    # ------------------------------------------------------------------

    async def _save_local(self, file: UploadFile, doc_id: str) -> str:
        dest_dir = Path(settings.STORAGE_LOCAL_PATH) / doc_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = file.filename or "upload"
        dest_path = dest_dir / filename

        async with aiofiles.open(dest_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                await out.write(chunk)

        logger.info("Saved file locally: %s", dest_path)
        return str(dest_path)

    async def _delete_local(self, file_path: str) -> None:
        path = Path(file_path)
        if path.exists():
            path.unlink()
            logger.info("Deleted local file: %s", file_path)
            # Remove the doc-specific directory if empty
            try:
                path.parent.rmdir()
            except OSError:
                pass  # Directory not empty — leave it
        else:
            logger.warning("Local file not found for deletion: %s", file_path)

    # ------------------------------------------------------------------
    # S3 storage
    # ------------------------------------------------------------------

    async def _save_s3(self, file: UploadFile, doc_id: str) -> str:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        filename = file.filename or "upload"
        s3_key = f"{doc_id}/{filename}"

        file_content = await file.read()

        try:
            s3_client.put_object(
                Bucket=settings.S3_BUCKET,
                Key=s3_key,
                Body=file_content,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 upload failed for key %s: %s", s3_key, exc)
            raise

        logger.info("Uploaded to S3: bucket=%s key=%s", settings.S3_BUCKET, s3_key)
        return s3_key

    async def _delete_s3(self, file_path: str) -> None:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        try:
            s3_client.delete_object(Bucket=settings.S3_BUCKET, Key=file_path)
            logger.info("Deleted from S3: bucket=%s key=%s", settings.S3_BUCKET, file_path)
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 delete failed for key %s: %s", file_path, exc)
            raise


# Module-level singleton
file_storage = FileStorage()
