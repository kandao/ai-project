# Services and Security

Covers the ingestion worker pipeline, agent tools, security layers, Kafka topics, Redis usage, and the full service interaction map.

---

## Ingestion Pipeline (Worker)

The worker subscribes to `doc.ingest` and `doc.delete` on Kafka. Each message is processed sequentially; the consumer commits after each message — including on error — to avoid reprocessing poison messages.

### `doc.ingest` flow

```
Kafka message: { doc_id, user_id, file_path, file_type, filename }
        │
        ▼
1. Extract text
   pdf   → pdfplumber (page-by-page)
   docx  → python-docx (paragraph extraction)
   txt   → read raw
   md    → read raw (markdown kept as-is)

2. Detect language
   langdetect → "ja" or "en" (default "en" on failure)

3. Chunk text
   ja → fugashi (MeCab) tokenizer, word-based with overlap
   en → word split with CHUNK_SIZE (default 512 tokens) and CHUNK_OVERLAP (default 64)

4. Generate embeddings  [retry: 5 attempts, 30s backoff on rate-limit errors]
   EMBEDDING_PROVIDER=openai  → OpenAI text-embedding-3-small (1536 dims, default)
   EMBEDDING_PROVIDER=voyage  → Voyage AI voyage-3
   EMBEDDING_PROVIDER=cohere  → Cohere embed-english-v3.0
   EMBEDDING_PROVIDER=mock    → deterministic zeros (tests only)

5. Store chunks
   INSERT chunks (doc_id, user_id, content, embedding, language, chunk_index, metadata)

6. UPDATE documents SET status='ready'
   (status='failed' on unrecoverable error)
```

### `doc.delete` flow

```
Kafka message: { doc_id }
        │
        ▼
DELETE chunks WHERE doc_id = ?
(document record already cascaded by backend DELETE)
```

---

## Agent — ReAct Loop and Tools

The agent runs a ReAct (Reason + Act) loop: the LLM decides which tool to call each turn, receives the result, and continues until it produces a final text response.

### Trigger modes

| Mode  | How to activate                          | Use case                               |
|-------|------------------------------------------|----------------------------------------|
| CLI   | `python agent.py` (no Kafka env var)     | Standalone analyst — local data, CSV, stocks |
| Kafka | `KAFKA_BOOTSTRAP_SERVERS=...` set        | DocQA chat — multi-user production    |

In Kafka mode the agent:
1. Consumes `chat.query` from Kafka
2. Calls `POST /api/internal/token-exchange` → gets `{ db_user, db_password, ... }`
3. Builds a `db_url` string and injects it into tool handlers via Python closures (the LLM never sees credentials)
4. Scans the user message for prompt injection
5. Runs `agent_loop()` with the full tool set
6. Publishes response chunks to `Redis: session:{session_id}` as `"chunk:<text>"`, ending with `"[DONE]"`

### Tools

| Tool              | Available in  | Description                                                   | Key args                              |
|-------------------|---------------|---------------------------------------------------------------|---------------------------------------|
| `hybrid_retrieval`| Kafka + CLI   | pgvector + BM25 with RRF fusion — searches uploaded docs      | `query` (str), `top_k` (int, 1–20)    |
| `query_database`  | Kafka + CLI   | Read-only SQL against PostgreSQL (SELECT only, validated)     | `sql` (str, max 2000 chars)           |
| `get_stock_price` | Kafka + CLI   | Real-time stock price via yfinance                            | `symbol` (str)                        |
| `analyze_csv`     | Kafka + CLI   | Load CSV, show shape/dtypes/stats, optional `df.eval()` query | `file_path`, `query` (optional)       |
| `generate_chart`  | Kafka + CLI   | Bar/line/pie chart from JSON data, saved as PNG               | `data` (JSON str), `chart_type`, `title`, `output_path` |
| `extract_pdf`     | Kafka + CLI   | Extract full text from a PDF file                             | `file_path`                           |
| `extract_doc`     | Kafka + CLI   | Extract full text from a DOCX file                            | `file_path`                           |
| `TodoWrite`       | Kafka + CLI   | Manage task lists                                             | —                                     |
| `load_skill`      | Kafka + CLI   | Load a named skill definition                                 | `name`                                |
| `bash_safe`       | CLI only      | Constrained shell execution                                   | `command` (str, max 1000 chars)       |
| `read_file`       | CLI only      | Read a file (path-sandboxed to WORKDIR)                       | `path`                                |
| `write_file`      | CLI only      | Write a file                                                  | `path`, `content`                     |
| `edit_file`       | CLI only      | Edit a file                                                   | `path`, edits                         |
| `background_run`  | CLI only      | Run a task in the background                                  | —                                     |
| `check_background`| CLI only      | Check background task status                                  | —                                     |
| `compress`        | CLI only      | Manually compress context                                     | —                                     |

### Hybrid retrieval SQL

The `hybrid_retrieval` tool runs a two-branch query fused with Reciprocal Rank Fusion (k=60):

```sql
WITH
vector_ranked AS (
    SELECT id, content, metadata,
           ROW_NUMBER() OVER (ORDER BY embedding <=> $query_vec::vector) AS rank
    FROM chunks
    ORDER BY embedding <=> $query_vec::vector
    LIMIT top_k * 2
),
bm25_ranked AS (
    SELECT id, content, metadata,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(
               to_tsvector('english', content),
               plainto_tsquery('english', $query)
           ) DESC) AS rank
    FROM chunks
    WHERE to_tsvector('english', content) @@ plainto_tsquery('english', $query)
    LIMIT top_k * 2
),
fused AS (
    SELECT
        COALESCE(v.id, b.id) AS id,
        COALESCE(v.content, b.content) AS content,
        COALESCE(v.metadata, b.metadata) AS metadata,
        COALESCE(1.0 / (60 + v.rank), 0) +
        COALESCE(1.0 / (60 + b.rank), 0) AS rrf_score
    FROM vector_ranked v
    FULL OUTER JOIN bm25_ranked b USING (id)
)
SELECT id, content, metadata, rrf_score
FROM fused ORDER BY rrf_score DESC LIMIT top_k
```

Retrieved chunks are scanned for prompt injection before being returned to the LLM.

---

## Security

### Rate Limiting (Backend)

Redis sliding-window rate limiter. Applied to document upload and chat endpoints.

```
Key:  ratelimit:{user_id}   (Redis sorted set, score = unix timestamp)

On each request:
  ZREMRANGEBYSCORE key 0 (now - 60s)   -- prune entries outside the window
  ZADD key now now                       -- record this request
  ZCARD key                             -- count requests in the window
  EXPIRE key 60                         -- auto-expire the key
  → HTTP 429 with Retry-After: 60 if count > RATE_LIMIT_REQUESTS_PER_MINUTE
```

Default limit: 30 req/min. Test overlay sets 1000 req/min to avoid interference with polling.

### Policy Engine (Agent)

Operates **outside the LLM** — the LLM cannot modify or bypass it. Three checks run before every tool call:

**1. Tool allowlist / denylist**

| Mode  | Denied (hard block)                                             |
|-------|-----------------------------------------------------------------|
| Kafka | `bash`, `background_run`, `write_file`, `edit_file`            |
| CLI   | *(none)*                                                        |

**2. Rate limits per session**

| Mode  | Max calls / session | Max calls / minute |
|-------|---------------------|--------------------|
| Kafka | 50                  | 20                 |
| CLI   | 200                 | 60                 |

**3. Argument validation** (applied per-tool before execution)

| Tool              | Argument  | Rules                                                                 |
|-------------------|-----------|-----------------------------------------------------------------------|
| `query_database`  | `sql`     | Must start with `SELECT`; blocks `DROP/DELETE/UPDATE/INSERT/ALTER/CREATE/TRUNCATE/GRANT/REVOKE`, `INTO OUTFILE`, `LOAD_FILE`, comment injection; max 2000 chars |
| `hybrid_retrieval`| `query`   | max 500 chars                                                         |
|                   | `top_k`   | integer, 1–20                                                         |
| `read_file`       | `path`    | Blocks `../`, `/etc/`, `/proc/`, `/sys/`, `/dev/`, `.env`, `.key`, `.pem`, SSH keys, shell histories; must resolve under `WORKDIR` |
| `bash_safe`       | `command` | max 1000 chars                                                        |

### Prompt Injection Scanner (Agent)

Scans user messages and RAG-retrieved chunks before they reach the LLM.

| Category              | Example patterns detected                                              |
|-----------------------|------------------------------------------------------------------------|
| Instruction override  | "ignore all previous instructions", "override system prompt"           |
| Role hijacking        | "you are now a", "act as if you are", "pretend you are"                |
| Tool manipulation     | "call ... for all users", "send results to http://..."                 |
| Data exfiltration     | "list all users", "dump the database", "show all passwords"            |
| Hidden markers        | `<system>`, `[INST]`, `### SYSTEM`                                     |

**User messages**: matches are logged as warnings; the ReAct loop continues (soft block).

**RAG chunks**: flagged chunks are replaced with `[CONTENT REMOVED: chunk N flagged by security scanner]` before LLM injection. The LLM sees the marker but not the injected content.

---

## Kafka Topics

| Topic        | Producer | Consumer | Payload fields                                             |
|--------------|----------|----------|------------------------------------------------------------|
| `doc.ingest` | backend  | worker   | `{ doc_id, user_id, file_path, file_type, filename }`     |
| `doc.delete` | backend  | worker   | `{ doc_id }`                                               |
| `chat.query` | backend  | agent    | `{ session_id, user_id, message, token }`                  |

Consumer groups: `worker-group` (worker), `agent-group` (agent). Both use `auto_offset_reset=earliest` and manual commit per message.

---

## Redis Usage

| Key pattern             | Set by  | Read by | Type         | Purpose                                                            |
|-------------------------|---------|---------|--------------|--------------------------------------------------------------------|
| `session:{session_id}`  | agent   | backend | pub/sub channel | Agent publishes `chunk:<text>` / `[DONE]`; backend relays as SSE |
| `ratelimit:{user_id}`   | backend | backend | Sorted set   | Sliding window counter; score = unix timestamp                    |

Redis DB: `0` in production, `1` in test overlay (isolated via `docker-compose.test.yml`).

---

## Service Interaction Map

```
┌─────────┐
│  Client │
└────┬────┘
     │ HTTPS  Authorization: Bearer <JWT>
     │
┌────▼──────────────────────────────────────────────────────────────┐
│  Backend  (FastAPI + uvicorn, port 8000)                          │
│                                                                   │
│  /api/auth/*             → bcrypt + JWT + CREATE ROLE             │
│  /api/documents/ POST    → store file → INSERT doc                │
│                            → Kafka: doc.ingest                    │
│  /api/documents/ GET     → SELECT documents WHERE user_id         │
│  /api/documents/ DELETE  → Kafka: doc.delete → DELETE doc         │
│  /api/chat POST          → rate limit → upsert session            │
│                            → INSERT message → INSERT token        │
│                            → Kafka: chat.query                    │
│                            → SUBSCRIBE Redis: session:{id}        │
│                            → StreamingResponse (SSE relay)        │
│  /api/chat/history       → SELECT messages WHERE session_id       │
│  /api/chat/sessions      → SELECT sessions WHERE user_id          │
│  /api/analytics/         → SUM/AVG messages.metadata              │
│  /api/internal/          → token exchange (Docker network only)   │
│  /health                 → { status: "ok" }                       │
└──┬──────────────────────────────────┬──────────────────────────────┘
   │ Kafka: doc.ingest / doc.delete   │ Kafka: chat.query
   ▼                                  ▼
┌──────────────────┐    ┌─────────────────────────────────────────────┐
│  Worker          │    │  Agent  (ReAct loop)                        │
│  (aiokafka)      │    │  (Kafka consumer or CLI REPL)               │
│                  │    │                                             │
│  doc.ingest:     │    │  1. Consume chat.query                      │
│  ├─ extract text │    │  2. POST /api/internal/token-exchange        │
│  ├─ detect lang  │    │     → { db_user, db_password, ... }         │
│  ├─ chunk        │    │  3. Inject creds into tool closures         │
│  ├─ embed        │    │  4. Scan user message for injection         │
│  └─ store chunks │    │  5. ReAct loop:                             │
│                  │    │     LLM → tool call → result → LLM → ...   │
│  doc.delete:     │    │     Tools: hybrid_retrieval, query_database │
│  └─ DELETE chunks│    │            get_stock_price, analyze_csv ... │
│                  │    │  6. Publish chunks → Redis: session:{id}    │
└────────┬─────────┘    └───────────────────────────────┬─────────────┘
         │                                              │
         │ INSERT chunks                INSERT/SELECT (scoped role)
         ▼                                              ▼
┌──────────────────────────────────────────────────────────────┐
│  PostgreSQL 16                                               │
│  users, tokens, documents, sessions, messages, chunks        │
│  pgvector (IVFFlat cosine) + pg_trgm (GIN trigram) + RLS    │
└──────────────────────────────────────────────────────────────┘

                          Agent publishes → Redis: session:{id}
                          Backend subscribes → SSE → Client
                          ┌──────────────────────┐
                          │  Redis 7             │
                          │  pub/sub: session:*  │
                          │  sorted sets: ratelimit:* │
                          └──────────────────────┘
```
