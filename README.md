# DocQA — Document Q&A with AI Agent

A multi-service system for document ingestion, retrieval-augmented generation, and AI-powered chat. Upload documents (PDF, DOCX, TXT, MD), and ask questions through a ReAct agent that combines document retrieval with database queries, stock lookups, and data analysis.

## Architecture

```
Client → FastAPI Backend → Kafka → Agent (ReAct loop) → Redis → SSE → Client
                          → Kafka → Worker (ingestion pipeline) → pgvector
```

| Service | Description | Port |
|---------|-------------|------|
| **backend** | FastAPI REST API — auth, uploads, chat SSE relay, token exchange | 8000 |
| **worker** | Kafka consumer — text extraction, chunking, embedding, vector storage | — |
| **agent** | Kafka consumer or CLI — ReAct loop with Anthropic/OpenAI LLMs | — |
| **postgres** | PostgreSQL 16 + pgvector + RLS for per-user data isolation | 5432 |
| **kafka** | Message bus (Confluent Kafka + Zookeeper) | 9092 |
| **redis** | SSE relay (pub/sub) + rate limiting | 6379 |

## Getting Started

### Prerequisites
- Docker & Docker Compose
- API keys: `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` for the LLM), `OPENAI_API_KEY` (for embeddings)

### Start All Services

```bash
# Set required env vars
export ANTHROPIC_API_KEY=your-key-here   # LLM for the agent
export OPENAI_API_KEY=your-key-here      # embeddings (text-embedding-3-small)

# Start the full stack (backend, worker, agent, postgres, kafka, zookeeper, redis)
docker compose up -d

# Check that all containers are running
docker compose ps

# Verify the backend is healthy
curl http://localhost:8000/health
```

Services start in dependency order: zookeeper → kafka → postgres/redis → backend/worker/agent. The healthchecks in `docker-compose.yml` ensure each service waits for its dependencies before starting.

### Start Individual Services

```bash
# Start only infrastructure (database, message bus, cache)
docker compose up -d zookeeper kafka postgres redis

# Start the backend only (requires postgres, redis, kafka)
docker compose up -d backend

# Start the ingestion worker only (requires postgres, kafka)
docker compose up -d worker

# Start the agent only (requires postgres, redis, kafka)
docker compose up -d agent
```

### Rebuild After Code Changes

```bash
# Rebuild and restart a single service
docker compose up -d --build backend

# Rebuild all custom services
docker compose up -d --build backend worker agent
```

### View Logs

```bash
# Follow logs for all services
docker compose logs -f

# Follow logs for a specific service
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f agent
```

### Stop Services

```bash
# Stop all services (preserves data volumes)
docker compose down

# Stop all services and remove data volumes
docker compose down -v
```

### Agent CLI Mode (standalone)

The agent can run independently without the full stack:

```bash
cd agent
cp .env.example .env    # configure API keys
pip install -r requirements.txt
python agent.py          # interactive REPL
```

REPL commands: `/compact` (compress context), `/tasks` (show todos), `q` (quit).

When `KAFKA_BOOTSTRAP_SERVERS` is set, `python agent.py` starts in Kafka consumer mode instead of the CLI REPL.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/documents/` | Upload a document (PDF/DOCX/TXT/MD) |
| GET | `/api/documents/` | List user's documents |
| DELETE | `/api/documents/{id}` | Delete a document |
| POST | `/api/chat` | Send a chat message (SSE response) |
| GET | `/api/analytics/usage` | Usage statistics |
| GET | `/health` | Liveness probe |

All endpoints except `/health` require JWT authentication via `Authorization: Bearer <token>`.

## Document Ingestion Pipeline

1. Upload via API → file stored locally or S3
2. Kafka message published to `doc.ingest`
3. Worker extracts text (pdfplumber / python-docx)
4. Language detection (Japanese → fugashi tokenizer, English → word split)
5. Chunking with configurable size/overlap
6. Embedding via OpenAI (`text-embedding-3-small`) — provider is configurable (also supports Voyage AI, Cohere)
7. Storage in pgvector (cosine similarity) + pg_trgm (trigram search)

## Security

- **Per-user DB isolation**: One-time tokens exchanged for scoped PostgreSQL credentials. Row-Level Security ensures users only access their own data.
- **Credential protection**: DB credentials never reach the LLM — injected via Python closures in tool handlers.
- **Policy engine**: Tool allowlists per mode (CLI vs Kafka), rate limits, argument validation, path sandboxing.
- **Prompt injection scanning**: User messages scanned before the ReAct loop.

## Testing

### Unit Tests (no infrastructure required)

Unit tests run entirely with mocks — no Docker, no database, no API keys needed.

```bash
pip install -r requirements-test.txt
pytest tests/unit/
```

| Suite | File | Tests | Covers |
|-------|------|-------|--------|
| **Agent** | `test_loop.py` | 8 | ReAct loop mechanics, multi-tool rounds, multi-step reasoning |
| | `test_base_tools.py` | 17 | bash_safe constraints, file I/O, path safety |
| | `test_domain_tools.py` | 35 | Stocks, SQLite queries, CSV analysis, chart generation, PDF/DOCX extraction |
| | `test_auth.py` | 8 | Token exchange, AuthError, build_db_url |
| | `test_llm_client.py` | 11 | Anthropic/OpenAI dispatch, streaming, model/URL overrides |
| | `test_background.py` | 7 | BackgroundManager run/check/drain, concurrent tasks |
| | `test_context.py` | 7 | Token estimation, microcompact, auto_compact |
| | `test_skills.py` | 7 | SkillLoader parse, load, descriptions |
| | `test_todo.py` | 11 | TodoManager CRUD, validation, has_open_items |
| **Worker** | `test_extractors.py` | 11 | PDF, DOCX, TXT, MD extraction and dispatch |
| | `test_language_detection.py` | 7 | English/Japanese detection, edge cases |
| | `test_chunking_english.py` | 8 | Word-based chunking, overlap, boundaries |
| | `test_chunking_japanese.py` | 5 | MeCab-based chunking (mocked when unavailable) |
| | `test_embedding.py` | 10 | Mock embedder, provider dispatch, retry |
| | `test_chunk_quality.py` | 6 | Content integrity, bounds, index monotonicity |
| **Total** | | **153** | |

### E2E Tests — Mock mode (requires full stack, no API keys)

E2E tests hit real infrastructure: PostgreSQL, Kafka, Redis, and all three services. Embedding and LLM calls use deterministic mocks — no external API keys needed.

```bash
# Start test infrastructure (uses docqa_test DB, Redis DB 1, mock providers)
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d

# Run E2E tests
pip install -r requirements-test.txt
pytest tests/e2e/ -m e2e
```

| File | Covers |
|------|--------|
| `test_auth.py` | JWT validation, token exchange, replay prevention, expiry |
| `test_document_lifecycle.py` | Upload, list, delete, ingestion status |
| `test_chat_flow.py` | POST /api/chat → Kafka → Agent → SSE relay |
| `test_rate_limiting.py` | Sliding window rate limiter per user |
| `test_analytics.py` | Token/latency aggregation, date filtering |
| `test_multi_user_isolation.py` | Cross-user document/session/chunk RLS |
| `test_error_handling.py` | Malformed requests, ingestion failures |

The `conftest.py` auto-truncates all tables and flushes Redis between E2E tests. The test overlay (`docker-compose.test.yml`) provides an isolated `docqa_test` database, Redis DB 1, and mock embedding/LLM providers so E2E tests never call external APIs.

### E2E Tests — Real-API mode (calls OpenAI + Anthropic)

A third overlay (`docker-compose.real-api.yml`) re-enables real providers on top of the test isolation layer. Uses `text-embedding-3-small` (OpenAI) for embeddings and `claude-sonnet-4-6` (Anthropic) for the agent LLM. The test database and Redis isolation from `docker-compose.test.yml` remain in effect.

```bash
# Step 1 — export your keys (never hardcode them)
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# Step 2 — start the stack (three-overlay chain)
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  -f docker-compose.real-api.yml \
  up -d --build

# Step 3 — verify the stack is healthy
docker compose ps
curl http://localhost:8000/health

# Step 4 — run the E2E suite
pytest tests/e2e/ -m e2e -v

# Step 5 — tear down and remove test volumes
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  -f docker-compose.real-api.yml \
  down -v
```

To run a single flow first:

```bash
# Ingestion only — triggers real OpenAI embedding call
pytest tests/e2e/test_document_lifecycle.py -v

# Chat flow only — triggers real Anthropic LLM call
pytest tests/e2e/test_chat_flow.py -v
```

### Run Everything

```bash
# Unit tests only (fast, no Docker, no API keys)
pytest tests/unit/

# E2E tests only — mock mode (requires Docker stack)
pytest tests/e2e/ -m e2e

# E2E tests only — real-API mode (requires keys + real-api overlay)
pytest tests/e2e/ -m e2e -v

# Run a single test file
pytest tests/unit/agent/test_loop.py -v

# Run tests matching a keyword
pytest -k "rate_limit"
```

## Configuration

All services use environment variables (pydantic-settings). See `.env.example` files in each service directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | LLM provider (`anthropic` or `openai`) |
| `EMBEDDING_PROVIDER` | `openai` | Embedding provider (`openai`, `voyage`, `cohere`, or `mock`) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name (provider-specific) |
| `CHUNK_SIZE` | `512` | Token count per chunk |
| `CHUNK_OVERLAP` | `64` | Overlap between chunks |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `30` | API rate limit per user |
| `MAX_UPLOAD_SIZE_MB` | `50` | Maximum upload file size |

## License

See [LICENSE](LICENSE).
