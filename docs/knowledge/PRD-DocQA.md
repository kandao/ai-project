# Agent PRD — DocQA tool (Enterprise Document Q&A)

## Purpose

Production-grade RAG system: upload documents, ask questions, get answers with source citations. Supports English and Japanese. LLM provider switchable via `LLM_PROVIDER` env var.

---

## User Stories

- As a user, I can upload PDF / DOCX / TXT / Markdown files
- As a user, I can ask questions in English or Japanese and receive cited answers
- As a user, I can continue a multi-turn conversation with follow-up questions
- As an admin, I can view token usage and cost per query
- As a developer, I can switch between Anthropic and OpenAI with one env var change

---

## File Structure

```
backend/
  ├── main.py                   # FastAPI app entrypoint
  ├── routers/
  │   ├── documents.py          # upload, list, delete
  │   └── chat.py               # chat, history, streaming
  ├── services/
  │   ├── ingestion.py          # extract → chunk → embed → store
  │   ├── retrieval.py          # hybrid search (pgvector + BM25 + RRF)
  │   └── conversation.py       # multi-turn memory, context window mgmt
  ├── llm/
  │   └── llm_client.py         # Anthropic + OpenAI abstraction
  ├── chunking/
  │   ├── japanese.py           # fugashi tokenizer pipeline
  │   └── english.py            # word/sentence splitter
  ├── models/                   # SQLAlchemy models
  ├── requirements.txt
  └── .env.example
```

---

## Environment Variables

```bash
LLM_PROVIDER=anthropic          # or "openai"
LLM_MODEL=claude-sonnet-4-6     # optional model override
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

DATABASE_URL=postgresql://...
REDIS_URL=redis://localhost:6379
KAFKA_BOOTSTRAP_SERVERS=localhost:9092

EMBEDDING_PROVIDER=voyage       # or "cohere" — for vector embeddings
EMBEDDING_MODEL=voyage-3
```

---

## API Endpoints

```
POST   /api/documents/upload     # multipart/form-data — PDF, DOCX, TXT, MD
GET    /api/documents            # list all indexed documents
DELETE /api/documents/{id}       # remove document + all associated vectors

POST   /api/chat                 # { "message": "...", "session_id": "..." }
GET    /api/chat/history         # ?session_id=...

GET    /api/analytics            # token usage, cost, latency per query
```

---

## Ingestion Pipeline

```
File upload → FastAPI
  → validate file type + size
  → store to S3 / local filesystem
  → publish to Kafka topic: doc.ingest

Kafka consumer (agent worker):
  1. Extract text
       PDF  → pdfplumber
       DOCX → python-docx
       TXT/MD → direct read
  2. Detect language (langdetect)
  3. Chunk text
       Japanese → fugashi morphological tokenizer → chunk by token count
       English  → split by words with overlap
  4. Generate embeddings (Voyage AI / Cohere)
  5. Store to pgvector with metadata (doc_id, chunk_index, language, source)
  6. Index BM25 via pg_search extension
```

**Chunk config** (test all three during Week 2):

| Size   | Overlap | Use case              |
|--------|---------|-----------------------|
| 256    | 32      | High-precision lookup |
| 512    | 64      | Balanced (default)    |
| 1024   | 128     | Long-form reasoning   |

---

## Chat Pipeline

The backend does not call the LLM or perform retrieval directly. It publishes to Kafka and relays the response stream.

```
POST /api/chat  { message, session_id }
  → FastAPI publishes → Kafka: chat.query
  → FastAPI subscribes → Redis: session:{id}  (SSE open)

Agent (Kafka consumer, ReAct loop):
  → LLM reads the query and decides which tool(s) to call
  → if document-related → calls hybrid_retrieval tool:
        normalize query (lemmatize if Japanese)
        pgvector cosine similarity top-k  ─┐
        pg_search BM25 top-k              ─┤ parallel
        merge with RRF                    ─┘
        LLM summarizes with citations [doc_id, chunk_index]
  → if not document-related → calls other tools (stocks, CSV, etc.)
  → stream response chunks → Redis: session:{id}

FastAPI relays Redis stream → SSE → Client
```

The agent is not told to always retrieve — it reasons about the query first.

---

## Multi-Turn Conversation

- Keep last N turns in context (configurable, default 10)
- Each turn stored in PostgreSQL with session_id
- Context compaction: summarize oldest turns when approaching token limit
- Follow-up questions re-use the same retrieval session

---

## Hybrid Search Detail

**Why hybrid**: pure vector search misses exact keyword matches (product codes, IDs, names). BM25 catches these; RRF combines both without needing tuned weights.

**RRF formula**:
```
score(d) = 1 / (k + rank_vector(d)) + 1 / (k + rank_bm25(d))
k = 60  (standard default)
```

**Infrastructure**: both pgvector and pg_search (ParadeDB) run inside the same PostgreSQL instance — no additional services.

---

## Japanese Language Support

| Stage | Approach |
|---|---|
| Chunking | fugashi (MeCab) tokenizes → chunk by token count, not character count |
| Query | Same tokenizer + lemmatization (dictionary form) before embedding |
| Language detection | langdetect → routes to correct chunking/normalization path |
| Embedding | Same embedding model for both languages (Voyage AI multilingual) |

**Why it matters**: without tokenization, chunk boundaries split mid-word (日本語 → garbage), producing mismatched embeddings between query and chunks.

---

## Production Features (v2)

- [ ] Prompt caching for system instructions (Anthropic only)
- [ ] Per-user rate limiting (Redis)
- [ ] JWT authentication (PyJWT)
- [ ] Cost tracking per query (token count × price per token)
- [ ] Structured logging (request_id, session_id, provider, model, latency_ms, tokens)
- [ ] A/B test framework: compare chunking strategies or Anthropic vs OpenAI responses

---

## Out of Scope (v1)

- SSO / SAML
- Multi-tenancy with RBAC
- Bilingual UI
- Slack integration

---

## Dependencies

```
anthropic
openai
fastapi
uvicorn
python-multipart
psycopg2-binary
pgvector
kafka-python
redis
pdfplumber
python-docx
fugashi[unidic-lite]
langdetect
PyJWT
python-dotenv
```
