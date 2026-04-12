# RAG Pipeline — E2E Test Plan

> Test plan for the Retrieval-Augmented Generation pipeline: document ingestion (Worker) + hybrid retrieval (Agent).

---

## Scope

This plan covers the full RAG data path:

```
Upload → Extract → Detect Language → Chunk → Embed → Store → Retrieve → Answer
         ───────── Worker (ingestion) ──────────────   ── Agent (retrieval) ──
```

Tests are grouped into **unit tests** (isolated components) and **integration tests** (cross-component flows). All tests avoid calling external embedding APIs — use `tests/mock_embedding.py` for deterministic vectors.

---

## 1. Text Extraction (`worker/extractors/`)

### Unit Tests — `tests/unit/worker/test_extractors.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 1.1 | Extract PDF with text | Valid PDF with "Hello World" on page 1 | Extracted text contains "Hello World" |
| 1.2 | Extract multi-page PDF | 3-page PDF, distinct content per page | All 3 pages' content present, separated by `\n\n` |
| 1.3 | Extract PDF — empty page | PDF with one blank page | Returns empty string or whitespace-only |
| 1.4 | Extract PDF — corrupted file | Random bytes named `.pdf` | Raises exception (not silent failure) |
| 1.5 | Extract DOCX | Valid DOCX with paragraphs | All paragraph text extracted |
| 1.6 | Extract DOCX — empty doc | DOCX with no paragraphs | Returns empty string |
| 1.7 | Extract TXT | Plain text file | Exact file content returned |
| 1.8 | Extract MD | Markdown with headers/lists | Full markdown text returned (no parsing) |
| 1.9 | Extract — unsupported type | `.xlsx` file type | Raises `ValueError("Unsupported file type")` |
| 1.10 | Extract — file not found | Non-existent path | Raises `FileNotFoundError` |

### Key files:
- `worker/extractors/__init__.py` — dispatch by file type
- `worker/extractors/pdf.py` — pdfplumber
- `worker/extractors/docx.py` — python-docx
- `worker/extractors/text.py` — plain text / markdown

---

## 2. Language Detection (`worker/pipeline.py`)

### Unit Tests — `tests/unit/worker/test_language_detection.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 2.1 | English text | "This is a sample document about..." | `"en"` |
| 2.2 | Japanese text | "これはテスト文書です。日本語のテキスト..." | `"ja"` |
| 2.3 | French text | "Ceci est un document de test..." | `"en"` (non-ja defaults to en) |
| 2.4 | Mixed text (majority English) | 80% English + 20% Japanese | `"en"` |
| 2.5 | Mixed text (majority Japanese) | 80% Japanese + 20% English | `"ja"` |
| 2.6 | Empty text | `""` | `"en"` (default fallback) |
| 2.7 | Very short text | `"Hi"` | `"en"` (graceful handling) |
| 2.8 | Unicode edge case | Emoji-heavy text | `"en"` (no crash) |

### Key function:
- `worker/pipeline.py:detect_language()` — uses `langdetect` on first 2000 chars

---

## 3. Chunking (`worker/chunking/`)

### Unit Tests — English Chunking: `tests/unit/worker/test_chunking_english.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.1 | Basic chunking | 100-word text, chunk_size=20, overlap=5 | ~6 chunks, each ≤20 words |
| 3.2 | Overlap correctness | 50-word text, chunk_size=20, overlap=5 | Last 5 words of chunk N = first 5 of chunk N+1 |
| 3.3 | Single chunk | 10-word text, chunk_size=20 | Exactly 1 chunk |
| 3.4 | Empty text | `""` | Empty list `[]` |
| 3.5 | Chunk metadata | Any text | Each `Chunk` has correct `index` (0,1,2...) and `token_count` |
| 3.6 | Large document | 10,000-word text, chunk_size=512, overlap=64 | ~22 chunks, no content loss |
| 3.7 | Exact boundary | Exactly `chunk_size` words | Exactly 1 chunk |
| 3.8 | Overlap > chunk_size guard | overlap=25, chunk_size=20 | Graceful handling (no infinite loop) |

### Unit Tests — Japanese Chunking: `tests/unit/worker/test_chunking_japanese.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.9 | Basic Japanese chunking | Japanese paragraph | Chunks split on morpheme boundaries |
| 3.10 | No spaces in output | Japanese text | Chunk text has no whitespace between tokens (concatenated) |
| 3.11 | Overlap correctness (JP) | Japanese text, overlap=10 | Last 10 tokens of chunk N = first 10 of chunk N+1 |
| 3.12 | Mixed JP/ASCII | Japanese with embedded English words | Tokenizer handles both correctly |
| 3.13 | Empty Japanese text | `""` | Empty list `[]` |

### Key files:
- `worker/chunking/__init__.py` — dispatch by language
- `worker/chunking/english.py` — word split with overlap
- `worker/chunking/japanese.py` — fugashi MeCab tokenizer
- `worker/models.py` — `Chunk` dataclass

---

## 4. Embedding (`worker/embedding/`)

### Unit Tests — `tests/unit/worker/test_embedding.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 4.1 | Mock embed_batch | 5 texts | Returns 5 vectors, each 1024-dim |
| 4.2 | Deterministic output | Same text twice | Identical vectors returned |
| 4.3 | Different texts → different vectors | Two distinct texts | Vectors differ (cosine < 1.0) |
| 4.4 | Large batch | 200 texts | Returns 200 vectors (tests batching logic) |
| 4.5 | Empty text | `[""]` | Returns 1 vector (no crash) |
| 4.6 | Voyage provider dispatch | `EMBEDDING_PROVIDER=voyage` | `VoyageEmbedder` selected |
| 4.7 | Cohere provider dispatch | `EMBEDDING_PROVIDER=cohere` | `CohereEmbedder` selected |
| 4.8 | Retry on transient failure | Simulate API error on attempt 1 | Succeeds on retry (exponential backoff) |

### Key files:
- `worker/embedding/__init__.py` — provider dispatch + retry logic
- `worker/embedding/voyage.py` — Voyage AI client (batch size 128)
- `worker/embedding/cohere.py` — Cohere client (batch size 96)
- `tests/mock_embedding.py` — deterministic mock

---

## 5. Storage (`worker/storage.py`)

### Integration Tests — `tests/integration/worker/test_storage.py`

Requires: PostgreSQL with pgvector extension

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 5.1 | Store chunks | Insert 5 chunks with embeddings | All 5 rows in `chunks` table with correct doc_id, user_id, embedding |
| 5.2 | Metadata JSONB | Insert chunk with metadata | `metadata` column contains `{"chunk_index": N, "token_count": M}` |
| 5.3 | Language column | Insert EN and JA chunks | `language` column correctly set per chunk |
| 5.4 | Delete by doc_id | Insert chunks, then delete | `chunks` table has 0 rows for that doc_id |
| 5.5 | Update document status → ready | Mark doc as "ready" | `documents.status` = "ready" |
| 5.6 | Update document status → failed | Mark doc as "failed" | `documents.status` = "failed" |
| 5.7 | Vector format | Insert 1024-dim vector | pgvector accepts and stores correctly |
| 5.8 | Concurrent inserts | 2 docs ingested in parallel | No conflicts, both complete |

### Key file:
- `worker/storage.py` — asyncpg batch insert, delete, status update

---

## 6. Full Ingestion Pipeline (`worker/pipeline.py`)

### Integration Tests — `tests/integration/worker/test_pipeline.py`

Requires: PostgreSQL, file system, mock embeddings

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 6.1 | TXT → ready | Process a `.txt` payload | Status → `ready`, chunks in DB |
| 6.2 | PDF → ready | Process a `.pdf` payload | Status → `ready`, chunks extracted from PDF |
| 6.3 | DOCX → ready | Process a `.docx` payload | Status → `ready`, paragraphs chunked |
| 6.4 | MD → ready | Process a `.md` payload | Status → `ready`, markdown text chunked |
| 6.5 | Japanese document | Process Japanese `.txt` | Language detected as `ja`, chunks via MeCab |
| 6.6 | English document | Process English `.txt` | Language detected as `en`, chunks via word split |
| 6.7 | Corrupted file → failed | Process corrupted PDF | Status → `failed`, exception logged |
| 6.8 | Empty file → ready (no chunks) | Process empty `.txt` | Status → `ready`, 0 chunks (empty text path) |
| 6.9 | Large document | Process 50KB text file | All chunks stored, no timeout |
| 6.10 | Chunk count accuracy | Process 500-word text, chunk_size=128, overlap=16 | Expected ~4-5 chunks match actual |
| 6.11 | Embedding dimension consistency | Process any document | All embeddings are exactly 1024-dim |
| 6.12 | Delete pipeline | Call `pipeline.delete({"doc_id": ...})` | All chunks removed |

---

## 7. Kafka Consumer (`worker/main.py`)

### Integration Tests — `tests/integration/worker/test_kafka_consumer.py`

Requires: Kafka, PostgreSQL

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 7.1 | Consume doc.ingest | Publish valid message to `doc.ingest` | Worker processes, status → `ready` |
| 7.2 | Consume doc.delete | Publish delete message | Chunks removed from DB |
| 7.3 | Poison message | Publish malformed JSON | Offset committed, no crash, error logged |
| 7.4 | Missing file_path | Publish message with non-existent file path | Status → `failed`, offset committed |
| 7.5 | Graceful shutdown | Send SIGTERM during processing | Consumer stops cleanly, in-flight message committed |

---

## 8. Hybrid Retrieval (`agent/tools/retrieval.py`)

### Integration Tests — `tests/integration/agent/test_retrieval.py`

Requires: PostgreSQL with pgvector, pre-seeded chunks

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 8.1 | Vector search returns results | Seed chunks, query with semantically similar text | Top results have high cosine similarity |
| 8.2 | BM25 keyword match | Seed chunks with specific keywords, query those keywords | Matching chunks appear in results |
| 8.3 | RRF fusion ranking | Seed chunks that rank differently in vector vs BM25 | RRF score = 1/(60+rank_vec) + 1/(60+rank_bm25) |
| 8.4 | top_k parameter | Query with top_k=3 | Exactly 3 results returned |
| 8.5 | No results | Query with unrelated text, empty DB | Returns "No results found." |
| 8.6 | Per-user scoping (db_url) | Two users' chunks in DB, query with User A's db_url | Only User A's chunks returned (via RLS) |
| 8.7 | Result format | Query with results | Output has `[1] (score: X.XXXX)` headers + content |
| 8.8 | Embedding provider mismatch | Query embedding != document embedding dimension | Graceful error message |
| 8.9 | SQL injection safety | Query containing SQL injection attempt | No data leak, safe error handling |
| 8.10 | Large result set | Seed 1000 chunks, query | Returns top_k without timeout |

---

## 9. End-to-End RAG Flow

### E2E Tests — `tests/e2e/test_rag_flow.py`

Requires: Full stack (Backend + Worker + Agent + Kafka + Redis + PostgreSQL)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 9.1 | Upload → Ingest → Retrieve | 1. Upload TXT via API  2. Wait for `ready`  3. Chat asking about the content | Agent uses `hybrid_retrieval`, response references document content |
| 9.2 | Multi-document retrieval | Upload 3 docs, query spanning content from all 3 | Retrieved chunks from multiple documents |
| 9.3 | PDF ingestion + retrieval | Upload PDF, wait ready, ask about PDF content | Content extracted and retrievable |
| 9.4 | DOCX ingestion + retrieval | Upload DOCX, wait ready, ask about DOCX content | Content extracted and retrievable |
| 9.5 | Japanese document RAG | Upload Japanese TXT, wait ready, query in Japanese | Japanese chunks retrieved via MeCab tokenization |
| 9.6 | Delete removes from retrieval | Upload doc, wait ready, delete, query | No results from deleted document |
| 9.7 | User isolation in RAG | User A uploads doc, User B queries same topic | User B gets no results from User A's documents |
| 9.8 | Retrieval quality — exact match | Upload doc with "Paris is the capital of France", query "capital of France" | First result contains "Paris" |
| 9.9 | Retrieval quality — semantic | Upload doc about "machine learning algorithms", query "AI training methods" | Relevant chunks returned despite different wording |
| 9.10 | Large document RAG | Upload 100KB document, query specific section | Correct section chunk returned |
| 9.11 | Re-upload same document | Upload, wait ready, delete, re-upload, wait ready | New chunks stored, old chunks gone, retrieval works |
| 9.12 | Concurrent ingestion | Upload 5 docs simultaneously | All reach `ready`, all searchable |

---

## 10. Chunk Quality Tests

### Unit Tests — `tests/unit/worker/test_chunk_quality.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 10.1 | No content loss | Concatenate all chunk texts (minus overlap) | Reconstruct original text (up to whitespace normalization) |
| 10.2 | Overlap consistency | Compare overlap regions between adjacent chunks | Overlap tokens match exactly |
| 10.3 | Chunk size bounds | All chunks in a processed document | Every chunk has `token_count ≤ chunk_size` |
| 10.4 | Empty chunks | Process any document | No chunk has empty `text` field |
| 10.5 | Index monotonicity | Check chunk indices | Indices are 0, 1, 2, ... monotonically increasing |
| 10.6 | Config override | Set `CHUNK_SIZE=256, CHUNK_OVERLAP=32` | Chunks respect overridden config |

---

## Test Infrastructure Notes

### Mock Embedding Strategy

All tests use `tests/mock_embedding.py` which provides:
- Deterministic 1024-dim vectors based on SHA-256 of input text
- Unit-normalized vectors for valid cosine similarity
- No external API calls

Patch at test level:
```python
@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    from tests import mock_embedding
    monkeypatch.setattr("embedding.embed_batch", mock_embedding.embed_batch)
```

### Test Database Setup

```sql
-- Before each integration test:
TRUNCATE chunks, documents CASCADE;

-- Seed a test user:
INSERT INTO users (id, email, name, db_role)
VALUES ('...', 'test@example.com', 'Test', 'user_test');

-- Seed a document record:
INSERT INTO documents (id, user_id, filename, file_path, file_type, file_size, status)
VALUES ('...', '...', 'test.txt', '/tmp/test.txt', 'txt', 1024, 'processing');
```

### File Fixture Strategy

Test files should be created in `tests/fixtures/`:
```
tests/fixtures/
  sample.pdf          # valid PDF with known text
  sample.docx         # valid DOCX with known paragraphs
  sample.txt          # plain text with known word count
  sample_ja.txt       # Japanese text for language detection + chunking
  corrupted.pdf       # random bytes with .pdf extension
  empty.txt           # 0-byte file
  large.txt           # 50KB+ text for performance tests
```

---

## Priority Matrix

| Priority | Tests | Rationale |
|----------|-------|-----------|
| **P0 — Must have** | 6.1-6.7, 8.1-8.5, 9.1, 9.6, 9.7 | Core pipeline correctness + retrieval + security |
| **P1 — Should have** | 1.1-1.9, 3.1-3.6, 5.1-5.6, 9.2-9.5, 9.8 | Component-level confidence |
| **P2 — Nice to have** | 2.1-2.8, 4.1-4.8, 10.1-10.6, 9.9-9.12 | Edge cases + quality |
