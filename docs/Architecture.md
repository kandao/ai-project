# System Architecture

Two projects, one integrated system. The Agent serves both as a standalone CLI tool and as the intelligence layer behind DocQA's chat pipeline.

---

## Integrated Flow

```
┌──────────────────────────────────────────────────────────────┐
│                         Client                               │
└──────────────┬───────────────────────────────────────────────┘
               │ HTTP
┌──────────────▼───────────────────────────────────────────────┐
│                    FastAPI (backend)                         │
│                                                              │
│  POST /api/documents/upload                                  │
│    └── publish ──► Kafka: doc.ingest                         │
│                                                              │
│  POST /api/chat                                              │
│    ├── generate one-time token (mapped to user's DB role)    │
│    ├── publish ──► Kafka: chat.query  (+ session_id + token) │
│    └── subscribe ──► Redis: session:{id} ──► SSE stream      │
│                                                              │
│  POST /api/internal/token-exchange  (agent-only, internal)   │
│    └── consume token → return scoped DB credentials          │
└──────────┬───────────────────────────────────────────────────┘
           │ Kafka                          ▲ HTTP (internal)
         ┌─┴─────────────────┐              │ token exchange
         ▼ doc.ingest        ▼ chat.query   │
┌────────────────┐  ┌──────────────────────────────────────────┐
│ Ingestion      │  │ Agent  (ReAct loop)                      │
│ Worker         │  │                                          │
│                │  │  1. Extract token from Kafka message      │
│ - extract text │  │  2. POST /api/internal/token-exchange ────┘
│ - chunk        │  │     → receive scoped DB credentials
│ - embed        │  │     (Python only — never sent to LLM)
│ - store        │  │  3. Inject credentials into tool handlers │
│   pgvector     │  │  4. Run ReAct loop with scoped tools      │
│   pg_search    │  │                                          │
│                │  │  tools/ (use scoped DB credential)        │
└────────────────┘  │   ├── hybrid_retrieval  ◄─┐              │
                    │   ├── get_stock_price     │ LLM          │
                    │   ├── query_database      │ picks        │
                    │   └── ...                ◄─┘             │
                    │                                          │
                    │  stream chunks ──► Redis: session:{id}   │
                    └──────────────────────────────────────────┘
                                       │
                            ┌──────────▼───────────────┐
                            │  llm_client.py           │
                            │  (Anthropic / OpenAI)    │
                            │  via LLM_PROVIDER        │
                            └──────────────────────────┘
```

---

## Agent — Two Trigger Modes

The agent is an independent system. It can be triggered two ways:

| Mode | Trigger | Use case |
|---|---|---|
| **CLI** | `python agent.py` | Standalone analyst — CSV, stocks, SQL |
| **Kafka consumer** | `chat.query` topic | DocQA chat — agent decides what to do |

The agent does not import or depend on the FastAPI backend. Communication is Kafka (inbound) and Redis pub/sub (outbound), with one exception: the internal token-exchange API call for per-user access control (see below).

---

## Per-User Access Control

Each chat query carries a one-time token. The agent exchanges it for scoped DB credentials before the ReAct loop starts.

```
Kafka message: { message, session_id, token }
         │
         ▼
  agent (consumer.py)
         │
         │  POST /api/internal/token-exchange { token }
         │  ◄── { db_user, db_password, db_host, db_port, db_name }
         │
         ▼
  build scoped DB connection string (Python closure)
         │
         ▼
  inject into tool handlers: hybrid_retrieval, query_database
         │
         ▼
  agent_loop() — LLM calls tools normally
                 tools use scoped credential internally
                 LLM never sees db_user or db_password
```

### Security Rules

| Rule | How |
|---|---|
| Credentials never reach the LLM | Injected via Python closure in tool handler wrappers, not in tool schemas or messages |
| Token is single-use | Backend marks as consumed on exchange; reuse returns 401 |
| Scoped DB user | PostgreSQL role with permissions limited to that user's data |
| No credential in transcripts | `db_url` exists only in Python closures, never serialized to message history |
| CLI mode unaffected | No token exchange — uses local SQLite or env `DATABASE_URL` |

### Backend Responsibilities

- Create/manage per-user PostgreSQL roles
- Generate and store one-time tokens on each chat request
- Serve `POST /api/internal/token-exchange` (internal network only)
- Clean up DB users when no longer needed

---

## Chat Query Flow (step by step)

```
1. Client    POST /api/chat  { message, session_id }
2. Backend   generate one-time token for the authenticated user
             publish → Kafka: chat.query { message, session_id, token }
             subscribe → Redis: session:{id}  (SSE open)
3. Agent     consume from Kafka: chat.query
4. Agent     exchange token → POST /api/internal/token-exchange
             receive scoped DB credentials (Python only)
5. Agent     ReAct loop — LLM autonomously decides which tool(s) to call:

               "what does the refund policy say?"
                 → hybrid_retrieval  (uses scoped credential)

               "analyze sales_q1.csv and check AAPL price"
                 → analyze_csv + get_stock_price  (no DB needed)

               "summarize the uploaded report and compare with live data"
                 → hybrid_retrieval + get_stock_price  (mixed)

6. Agent     stream response → Redis: session:{id}
7. Backend   relay Redis stream → SSE → Client
```

Document retrieval is one tool among many — the agent decides whether to use it based on the query.

---

## Document Ingestion Flow

```
1. Client       POST /api/documents/upload  (PDF / DOCX / TXT / MD)
2. Backend      store file → S3 / local
                publish → Kafka: doc.ingest { file_path, doc_id }
3. Ingestion    extract text (pdfplumber / python-docx)
   Worker       detect language (langdetect)
                chunk text:
                  Japanese → fugashi tokenizer
                  English  → word split with overlap
                generate embeddings (Voyage AI / Cohere)
                store → pgvector + pg_search (same PostgreSQL)
```

---

## Docker Compose Services

| Service       | Image                       | Port | Purpose                        |
|---------------|-----------------------------|------|--------------------------------|
| `backend`     | custom (FastAPI + uvicorn)  | 8000 | REST API + SSE relay + token exchange |
| `agent`       | custom (Python)             | —    | Kafka consumer + ReAct loop    |
| `worker`      | custom (Python)             | —    | Kafka consumer for doc.ingest  |
| `postgres`    | pgvector/pgvector:pg16      | 5432 | Vector DB + BM25 + per-user roles |
| `kafka`       | confluentinc/cp-kafka       | 9092 | Message bus                    |
| `zookeeper`   | confluentinc/cp-zookeeper   | 2181 | Kafka coordinator              |
| `redis`       | redis:7-alpine              | 6379 | SSE relay + rate limiting      |

---

## LLM Provider Switching

Both the agent and the ingestion worker use `llm/llm_client.py` with the same pattern. Set once in Docker Compose env — applies to all consumers.

```bash
LLM_PROVIDER=anthropic   →  claude-sonnet-4-6  (default)
LLM_PROVIDER=openai      →  gpt-4o             (default)
LLM_MODEL=<id>           →  override model for the active provider
```

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Chat via Kafka | async publish/subscribe | Decouples backend from agent; agent scales independently |
| SSE relay via Redis | pub/sub per session_id | Backend stays stateless; agent streams without direct HTTP |
| Retrieval as a tool | agent decides when to call it | Agent handles any query type, not just document Q&A |
| Agent dual-mode | CLI + Kafka consumer | Agent runs standalone without the full stack |
| Ingestion separate worker | own Kafka consumer | Slow doc processing doesn't block chat latency |
| One-time token exchange | internal API call | DB credentials stay in Python runtime; LLM never sees them; per-user data isolation without leaking secrets into the prompt |
| Internal API exception | agent → backend HTTP | Only used for token exchange; all other communication remains Kafka/Redis |
