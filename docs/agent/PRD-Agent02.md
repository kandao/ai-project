# Agent PRD — Agent02 (Kafka + DocQA Integration)

> Builds on Agent01. Adds the Kafka consumer, hybrid retrieval tool, Redis streaming output, and Docker packaging — everything needed to plug the agent into the full DocQA stack described in Architecture.md.

---

## Purpose

Agent01 runs as a standalone CLI analyst. Agent02 wires it into the production pipeline:

```
Kafka: chat.query  →  consumer.py  →  ReAct loop  →  Redis: session:{id}
```

No changes to `loop.py` or existing tools — this PRD adds the missing integration layer.

---

## Scope

| Item | File | Description |
|---|---|---|
| Kafka consumer | `consumer.py` | Listen on `chat.query`, deserialize, call loop, stream to Redis |
| Hybrid retrieval tool | `tools/retrieval.py` | pgvector + BM25 + RRF fusion scoring |
| Redis streaming output | `consumer.py` | Publish response chunks to `session:{id}` channel |
| Dockerfile | `Dockerfile` | Container image for the `agent` Docker Compose service |

---

## File Changes

```
agent/
  ├── consumer.py               # NEW — Kafka consumer + Redis publisher
  ├── Dockerfile                 # NEW — container build
  ├── tools/
  │   └── retrieval.py          # NEW — hybrid retrieval
  └── (everything from Agent01 unchanged)
```

---

## consumer.py — Kafka Consumer

### Responsibilities

1. Connect to Kafka (`KAFKA_BOOTSTRAP_SERVERS`), subscribe to `chat.query` topic
2. Deserialize each message: `{ "message": str, "session_id": str }`
3. Build initial message list and call `agent_loop()` from `loop.py`
4. Intercept LLM output and stream text chunks to Redis pub/sub on `session:{session_id}`
5. On loop completion, publish a final `[DONE]` sentinel to the Redis channel

### Kafka Message Schema

```json
{
  "message": "what does the refund policy say?",
  "session_id": "abc-123"
}
```

### Redis Output

Publish to channel `session:{session_id}`:

| Message | Meaning |
|---|---|
| `chunk:<text>` | One LLM text chunk — forward to SSE client immediately |
| `error:<msg>` | Agent error — client should surface to user |
| `[DONE]` | Stream complete — client closes the SSE connection |

### Streaming Architecture

The goal is minimum time-to-first-token: each chunk from the LLM is forwarded to Redis the instant it arrives, with no buffering.

#### Two-phase LLM interaction

| Phase | LLM call | Reason |
|---|---|---|
| Tool-use rounds (intermediate) | `llm_client.chat()` — blocking | User doesn't see these; simplicity over latency |
| Final response (no more tools) | `llm_client.stream()` — token-by-token | Each chunk reaches the client immediately |

The agent detects the final round when the LLM response has `stop_reason == "end_turn"` (Anthropic) or `finish_reason == "stop"` (OpenAI) with no tool calls in the response.

#### Chunk dispatch loop

```python
# Final response round — stream directly to Redis
for chunk in llm_client.stream(messages, system=SYSTEM_PROMPT):
    redis_client.publish(f"session:{session_id}", f"chunk:{chunk}")
redis_client.publish(f"session:{session_id}", "[DONE]")
```

#### Error handling

```python
try:
    # ... agent loop ...
except Exception as e:
    redis_client.publish(f"session:{session_id}", f"error:{e}")
finally:
    redis_client.publish(f"session:{session_id}", "[DONE]")
```

#### Backend SSE relay (contract)

FastAPI subscribes to `session:{session_id}` and must:
- Forward each `chunk:<text>` as an SSE `data:` event **immediately** (no response buffering — use `StreamingResponse` with `media_type="text/event-stream"`)
- On `error:<msg>`: forward as SSE event with `event: error`
- On `[DONE]`: close the SSE stream

### Integration with loop.py

`consumer.py` reuses the same `agent_loop()` — no fork. The key difference is output routing:

| Mode | Input | Output |
|---|---|---|
| CLI | stdin | stdout (print) |
| Kafka | Kafka message | Redis pub/sub — streamed token by token |

### Error Handling

- If `agent_loop()` raises, publish error message to Redis channel and commit Kafka offset
- If Redis is unavailable, log error and skip (don't block Kafka consumer)
- Consumer runs in an infinite loop with graceful shutdown on SIGTERM

### Environment Variables (additional)

```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_GROUP_ID=agent-group              # consumer group
KAFKA_TOPIC=chat.query
REDIS_URL=redis://localhost:6379
```

---

## tools/retrieval.py — Hybrid Retrieval

### What it does

Combines vector similarity search (pgvector) with keyword search (BM25 via pg_search) using Reciprocal Rank Fusion (RRF) to retrieve relevant document chunks.

### Interface

```python
def hybrid_retrieval(query: str, top_k: int = 5) -> str:
    """
    1. Generate embedding for the query (via llm_client or external embedding API)
    2. pgvector similarity search → ranked list A
    3. BM25 keyword search via pg_search → ranked list B
    4. RRF fusion: score = Σ 1/(k + rank) for each list
    5. Return top_k chunks sorted by fused score
    """
```

### Tool Schema

```json
{
  "name": "hybrid_retrieval",
  "description": "Search uploaded documents using semantic + keyword hybrid retrieval. Use when the query is about uploaded documents or organizational knowledge.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Search query" },
      "top_k": { "type": "integer", "description": "Number of results (default 5)" }
    },
    "required": ["query"]
  }
}
```

### Database Schema (reads from)

The ingestion worker (separate service) writes to these tables. The retrieval tool only reads.

```sql
-- chunks table (populated by ingestion worker)
CREATE TABLE chunks (
    id          SERIAL PRIMARY KEY,
    doc_id      UUID NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB,
    embedding   vector(1024)       -- pgvector
);

-- BM25 index (pg_search)
CREATE INDEX idx_chunks_bm25 ON chunks USING bm25 (content);
```

### RRF Scoring

```
k = 60  (standard RRF constant)

For each chunk appearing in either result set:
  rrf_score = 1/(k + rank_vector) + 1/(k + rank_bm25)

If a chunk appears in only one list, the missing rank is treated as infinity (contributes 0).
```

### Embedding Generation

Query embeddings use the same provider as document ingestion to ensure consistency. Configured via:

```bash
EMBEDDING_PROVIDER=voyage          # or "cohere", "openai"
EMBEDDING_MODEL=voyage-3           # model for query embedding
```

### Environment Variables (additional)

```bash
DATABASE_URL=postgresql://user:pass@postgres:5432/docqa
EMBEDDING_PROVIDER=voyage
EMBEDDING_MODEL=voyage-3
VOYAGE_API_KEY=...                 # or COHERE_API_KEY, depending on provider
```

---

## Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Kafka mode — agent.py auto-detects via KAFKA_BOOTSTRAP_SERVERS
CMD ["python", "agent.py"]
```

### Docker Compose entry (in root docker-compose.yml)

```yaml
agent:
  build: ./agent
  environment:
    - LLM_PROVIDER=anthropic
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    - REDIS_URL=redis://redis:6379
    - DATABASE_URL=postgresql://user:pass@postgres:5432/docqa
  depends_on:
    - kafka
    - redis
    - postgres
```

---

## Registration in tools/__init__.py

Agent02 adds `hybrid_retrieval` to the existing registry:

```python
# tools/__init__.py — add to existing imports
from tools.retrieval import hybrid_retrieval

# Add to TOOL_HANDLERS
TOOL_HANDLERS["hybrid_retrieval"] = lambda **kw: hybrid_retrieval(kw["query"], kw.get("top_k", 5))

# Add to TOOLS list
TOOLS.append({
    "name": "hybrid_retrieval",
    "description": "Search uploaded documents using semantic + keyword hybrid retrieval.",
    "input_schema": { ... }  # as defined above
})
```

---

## Dependencies (additional)

```
kafka-python
redis
psycopg2-binary
pgvector
voyageai              # or cohere, depending on EMBEDDING_PROVIDER
```

These are added to `requirements.txt` alongside Agent01 dependencies.

---

## Out of Scope (Agent02)

- Ingestion worker (`doc.ingest` consumer) — separate service, separate PRD
- FastAPI backend — separate project
- Session history / multi-turn memory across Kafka messages
- Authentication or rate limiting (handled by backend)
- Horizontal scaling / consumer group rebalancing tuning
