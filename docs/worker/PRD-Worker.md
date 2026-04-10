# Worker PRD — Ingestion Worker (Document Processing Pipeline)

> Standalone Kafka consumer that processes uploaded documents into searchable chunks. Reads from `doc.ingest`, extracts text, chunks by language, generates embeddings, and stores vectors + BM25 index in PostgreSQL. No LLM calls — pure ETL pipeline.

---

## Purpose

When a user uploads a document via the backend, it publishes a message to Kafka topic `doc.ingest`. The ingestion worker picks it up and runs the full processing pipeline:

```
File → extract text → detect language → chunk → embed → store (pgvector + pg_search)
```

The worker is stateless and independent. It shares no code with the agent or backend. Its only contract is the Kafka message schema (input) and the PostgreSQL `chunks` table (output).

---

## File Structure

```
worker/
  ├── main.py                       # Kafka consumer loop + graceful shutdown
  ├── config.py                     # Settings from env vars
  ├── pipeline.py                   # Orchestrates: extract → detect → chunk → embed → store
  ├── extractors/
  │   ├── __init__.py               # dispatch by file type
  │   ├── pdf.py                    # pdfplumber
  │   ├── docx.py                   # python-docx
  │   └── text.py                   # plain text / markdown (direct read)
  ├── chunking/
  │   ├── __init__.py               # dispatch by language
  │   ├── japanese.py               # fugashi tokenizer → chunk by token count
  │   └── english.py                # word splitter with overlap
  ├── embedding/
  │   ├── __init__.py               # dispatch by EMBEDDING_PROVIDER
  │   ├── voyage.py                 # Voyage AI client
  │   └── cohere.py                 # Cohere client
  ├── storage.py                    # pgvector + pg_search writes
  ├── models.py                     # SQLAlchemy: chunks table schema
  ├── Dockerfile
  ├── requirements.txt
  └── .env.example
```

---

## Environment Variables

```bash
# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_GROUP_ID=worker-group
KAFKA_TOPIC_INGEST=doc.ingest
KAFKA_TOPIC_DELETE=doc.delete

# Database
DATABASE_URL=postgresql://admin:pass@postgres:5432/docqa

# Embedding
EMBEDDING_PROVIDER=voyage              # or "cohere"
EMBEDDING_MODEL=voyage-3
VOYAGE_API_KEY=...                     # if EMBEDDING_PROVIDER=voyage
COHERE_API_KEY=...                     # if EMBEDDING_PROVIDER=cohere

# File storage (read-only — files written by backend)
STORAGE_BACKEND=local
STORAGE_LOCAL_PATH=/data/uploads
S3_BUCKET=docqa-uploads
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Chunking defaults
CHUNK_SIZE=512                         # tokens
CHUNK_OVERLAP=64                       # tokens
```

---

## Kafka Consumer

### Topics

| Topic | Action |
|---|---|
| `doc.ingest` | Run full ingestion pipeline for the document |
| `doc.delete` | Delete all chunks and vectors for the document |

### Message Schema — doc.ingest

```json
{
  "doc_id": "uuid-...",
  "user_id": "uuid-...",
  "file_path": "/data/uploads/abc123.pdf",
  "file_type": "pdf",
  "filename": "quarterly_report.pdf"
}
```

### Message Schema — doc.delete

```json
{
  "doc_id": "uuid-..."
}
```

### Consumer Implementation

```python
# main.py

from kafka import KafkaConsumer
import json
import signal

consumer = KafkaConsumer(
    config.KAFKA_TOPIC_INGEST,
    config.KAFKA_TOPIC_DELETE,
    bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
    group_id=config.KAFKA_GROUP_ID,
    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    auto_offset_reset="earliest",
    enable_auto_commit=False,
)

running = True

def shutdown(signum, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, shutdown)

for msg in consumer:
    if not running:
        break

    try:
        if msg.topic == config.KAFKA_TOPIC_INGEST:
            pipeline.process(msg.value)
        elif msg.topic == config.KAFKA_TOPIC_DELETE:
            storage.delete_by_doc_id(msg.value["doc_id"])

        consumer.commit()

    except Exception as e:
        log.error(f"Failed to process {msg.topic}: {e}", exc_info=True)
        update_document_status(msg.value.get("doc_id"), "failed")
        consumer.commit()  # don't retry poison messages

consumer.close()
```

---

## Pipeline — Extract, Chunk, Embed, Store

```python
# pipeline.py

def process(payload: dict):
    doc_id = payload["doc_id"]
    user_id = payload["user_id"]
    file_path = payload["file_path"]
    file_type = payload["file_type"]

    # Step 1: Extract text
    text = extractors.extract(file_path, file_type)

    # Step 2: Detect language
    language = detect_language(text)

    # Step 3: Chunk
    chunks = chunking.chunk(text, language)

    # Step 4: Generate embeddings (batched)
    embeddings = embedding.embed_batch([c.text for c in chunks])

    # Step 5: Store to PostgreSQL (pgvector + pg_search)
    storage.store_chunks(
        doc_id=doc_id,
        user_id=user_id,
        chunks=chunks,
        embeddings=embeddings,
        language=language,
    )

    # Step 6: Update document status
    update_document_status(doc_id, "ready")
```

---

## Text Extraction

### Dispatch

```python
# extractors/__init__.py

from extractors.pdf import extract_pdf
from extractors.docx import extract_docx
from extractors.text import extract_text

EXTRACTORS = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "txt": extract_text,
    "md": extract_text,
}

def extract(file_path: str, file_type: str) -> str:
    extractor = EXTRACTORS.get(file_type)
    if not extractor:
        raise ValueError(f"Unsupported file type: {file_type}")
    return extractor(file_path)
```

### PDF — pdfplumber

```python
# extractors/pdf.py

import pdfplumber

def extract_pdf(file_path: str) -> str:
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)
```

### DOCX — python-docx

```python
# extractors/docx.py

from docx import Document

def extract_docx(file_path: str) -> str:
    doc = Document(file_path)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
```

### TXT / Markdown

```python
# extractors/text.py

def extract_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()
```

---

## Language Detection

```python
# pipeline.py

from langdetect import detect

def detect_language(text: str) -> str:
    """Detect language. Returns 'ja' for Japanese, 'en' for everything else."""
    try:
        lang = detect(text[:2000])  # sample first 2000 chars for speed
        return "ja" if lang == "ja" else "en"
    except Exception:
        return "en"  # default to English
```

Only two paths: Japanese (requires morphological tokenization) and English (word split). All other languages use the English path — good enough for v1.

---

## Chunking

### Dispatch

```python
# chunking/__init__.py

from chunking.japanese import chunk_japanese
from chunking.english import chunk_english

def chunk(text: str, language: str) -> list[Chunk]:
    if language == "ja":
        return chunk_japanese(text)
    return chunk_english(text)
```

### Chunk Data Class

```python
from dataclasses import dataclass

@dataclass
class Chunk:
    text: str
    index: int          # position in document
    token_count: int    # number of tokens in this chunk
```

### Japanese — fugashi (MeCab)

```python
# chunking/japanese.py

import fugashi

tagger = fugashi.Tagger()

def chunk_japanese(
    text: str,
    chunk_size: int = config.CHUNK_SIZE,
    overlap: int = config.CHUNK_OVERLAP,
) -> list[Chunk]:
    """
    Tokenize with MeCab, then group tokens into chunks.
    Overlap is in tokens, not characters.
    """
    tokens = [word.surface for word in tagger(text)]
    chunks = []
    start = 0
    index = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = "".join(chunk_tokens)  # Japanese: no spaces between tokens

        chunks.append(Chunk(
            text=chunk_text,
            index=index,
            token_count=len(chunk_tokens),
        ))

        start += chunk_size - overlap
        index += 1

    return chunks
```

**Why fugashi**: Japanese has no whitespace between words. Naive character-level splitting breaks words mid-morpheme, producing garbage embeddings. MeCab tokenization ensures chunk boundaries fall on word boundaries.

### English — Word Splitter

```python
# chunking/english.py

def chunk_english(
    text: str,
    chunk_size: int = config.CHUNK_SIZE,
    overlap: int = config.CHUNK_OVERLAP,
) -> list[Chunk]:
    """
    Split by whitespace, group into chunks of ~chunk_size words.
    Overlap is in words.
    """
    words = text.split()
    chunks = []
    start = 0
    index = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)

        chunks.append(Chunk(
            text=chunk_text,
            index=index,
            token_count=len(chunk_words),
        ))

        start += chunk_size - overlap
        index += 1

    return chunks
```

### Chunk Size Configuration

| Size | Overlap | Use case |
|---|---|---|
| 256 | 32 | High-precision lookup |
| 512 | 64 | Balanced (default) |
| 1024 | 128 | Long-form reasoning |

Configurable via `CHUNK_SIZE` and `CHUNK_OVERLAP` env vars. Default is 512/64.

---

## Embedding Generation

### Dispatch

```python
# embedding/__init__.py

from embedding.voyage import VoyageEmbedder
from embedding.cohere import CohereEmbedder

PROVIDERS = {
    "voyage": VoyageEmbedder,
    "cohere": CohereEmbedder,
}

embedder = PROVIDERS[config.EMBEDDING_PROVIDER](config.EMBEDDING_MODEL)

def embed_batch(texts: list[str]) -> list[list[float]]:
    return embedder.embed_batch(texts)
```

### Voyage AI

```python
# embedding/voyage.py

import voyageai

class VoyageEmbedder:
    def __init__(self, model: str):
        self.client = voyageai.Client()
        self.model = model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Voyage supports up to 128 texts per call."""
        all_embeddings = []
        batch_size = 128

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            result = self.client.embed(batch, model=self.model, input_type="document")
            all_embeddings.extend(result.embeddings)

        return all_embeddings
```

### Cohere

```python
# embedding/cohere.py

import cohere

class CohereEmbedder:
    def __init__(self, model: str):
        self.client = cohere.Client()
        self.model = model

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Cohere supports up to 96 texts per call."""
        all_embeddings = []
        batch_size = 96

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            result = self.client.embed(
                texts=batch,
                model=self.model,
                input_type="search_document",
            )
            all_embeddings.extend(result.embeddings)

        return all_embeddings
```

---

## Storage — pgvector + pg_search

### Database Schema

```sql
-- chunks table
CREATE TABLE chunks (
    id          SERIAL PRIMARY KEY,
    doc_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    embedding   vector(1024),
    language    VARCHAR(5) NOT NULL DEFAULT 'en',
    chunk_index INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vector similarity index (pgvector)
CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- BM25 full-text index (pg_search / ParadeDB)
CREATE INDEX idx_chunks_bm25 ON chunks USING bm25 (content);

-- Lookup by document
CREATE INDEX idx_chunks_doc_id ON chunks (doc_id);

-- RLS for per-user isolation
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY user_isolation ON chunks
    FOR SELECT USING (user_id::text = current_user);
```

### Write Implementation

```python
# storage.py

from pgvector.sqlalchemy import Vector
from sqlalchemy import insert

def store_chunks(
    doc_id: str,
    user_id: str,
    chunks: list[Chunk],
    embeddings: list[list[float]],
    language: str,
):
    """Batch insert chunks with embeddings into PostgreSQL."""
    rows = [
        {
            "doc_id": doc_id,
            "user_id": user_id,
            "content": chunk.text,
            "metadata": {
                "chunk_index": chunk.index,
                "token_count": chunk.token_count,
            },
            "embedding": embedding,
            "language": language,
            "chunk_index": chunk.index,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]

    with db_engine.begin() as conn:
        conn.execute(insert(chunks_table), rows)


def delete_by_doc_id(doc_id: str):
    """Delete all chunks for a document (triggered by doc.delete)."""
    with db_engine.begin() as conn:
        conn.execute(
            chunks_table.delete().where(chunks_table.c.doc_id == doc_id)
        )
```

### Document Status Update

After processing completes (success or failure), the worker updates the document status in the `documents` table:

```python
def update_document_status(doc_id: str, status: str):
    """Update document status: 'ready' or 'failed'."""
    with db_engine.begin() as conn:
        conn.execute(
            documents_table.update()
            .where(documents_table.c.id == doc_id)
            .values(status=status)
        )
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Unsupported file type | Mark document as `failed`, commit offset, log error |
| Text extraction fails (corrupt PDF) | Mark document as `failed`, commit offset, log error |
| Embedding API error (rate limit / timeout) | Retry with exponential backoff (3 attempts), then mark `failed` |
| Database write fails | Retry once, then mark `failed` |
| Kafka consumer disconnect | Auto-reconnect (built into kafka-python) |

Poison messages are never retried indefinitely — after failure, the offset is committed and the document is marked `failed`. Users can re-upload.

### Retry for Embedding API

```python
# embedding/__init__.py

import time

MAX_RETRIES = 3
BASE_DELAY = 2  # seconds

def embed_batch_with_retry(texts: list[str]) -> list[list[float]]:
    for attempt in range(MAX_RETRIES):
        try:
            return embedder.embed_batch(texts)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = BASE_DELAY * (2 ** attempt)
            log.warning(f"Embedding failed (attempt {attempt + 1}), retrying in {delay}s: {e}")
            time.sleep(delay)
```

---

## Dockerfile

```dockerfile
FROM python:3.12-slim

# fugashi requires MeCab C library
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmecab-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "main.py"]
```

### Docker Compose entry

```yaml
worker:
  build: ./worker
  environment:
    - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    - DATABASE_URL=postgresql://admin:pass@postgres:5432/docqa
    - EMBEDDING_PROVIDER=voyage
    - EMBEDDING_MODEL=voyage-3
    - VOYAGE_API_KEY=${VOYAGE_API_KEY}
    - STORAGE_BACKEND=local
    - STORAGE_LOCAL_PATH=/data/uploads
  volumes:
    - upload-data:/data/uploads       # shared with backend (read-only)
  depends_on:
    - kafka
    - postgres
```

---

## Dependencies

```
kafka-python
sqlalchemy
psycopg2-binary
pgvector
pdfplumber
python-docx
fugashi[unidic-lite]
langdetect
voyageai                  # if EMBEDDING_PROVIDER=voyage
cohere                    # if EMBEDDING_PROVIDER=cohere
python-dotenv
```

---

## Out of Scope (v1)

- OCR for scanned PDFs (assumes text-based PDFs)
- Excel / CSV / HTML file extraction
- Table extraction from PDFs (pdfplumber supports it, deferred)
- Incremental re-ingestion (update existing chunks when document is re-uploaded)
- Chunk deduplication across documents
- Custom chunking strategies per document type
- Embedding model fine-tuning
- Horizontal worker scaling tuning (Kafka consumer group handles basic scaling)
