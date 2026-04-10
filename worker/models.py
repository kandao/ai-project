from dataclasses import dataclass
import sqlalchemy as sa
from sqlalchemy import MetaData

metadata = MetaData()

# chunks table — embedding column handled via raw SQL (pgvector), not declared here
chunks_table = sa.Table(
    "chunks",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("doc_id", sa.UUID(as_uuid=True), nullable=False),
    sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("metadata", sa.JSON, server_default="{}"),
    sa.Column("language", sa.String(5), server_default="en"),
    sa.Column("chunk_index", sa.Integer, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
    ),
)

# documents table reference — only the columns needed for status updates
documents_table = sa.Table(
    "documents",
    metadata,
    sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
    sa.Column("status", sa.String),
)


@dataclass
class Chunk:
    text: str
    index: int
    token_count: int
