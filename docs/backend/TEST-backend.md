# E2E Test Plan — DocQA System

## Context

The DocQA project is a production RAG system with 3 microservices (Backend, Worker, Agent) communicating via Kafka and Redis, backed by PostgreSQL+pgvector. There are currently **zero tests** in the codebase. This plan creates a comprehensive E2E test suite that validates the full system flows end-to-end using Docker Compose infrastructure.

---

## Architecture & Test Infrastructure

### Test stack
- **pytest** + **pytest-asyncio** — test runner
- **httpx** (AsyncClient) — HTTP client for FastAPI (replaces TestClient for async)
- **testcontainers** or **docker-compose** — spin up Postgres, Redis, Kafka for real integration
- **Factory helpers** — seed users, JWT tokens, documents
- Location: `ai-project/tests/` (new top-level test directory)

### Test docker-compose override
Create `docker-compose.test.yml` that:
- Uses isolated DB (`docqa_test`), separate volumes
- Sets deterministic env vars (JWT_SECRET, etc.)
- Exposes all service ports for test access
- Optionally uses mock embedding provider (to avoid API costs)

### Directory structure
```
ai-project/tests/
  conftest.py              # Shared fixtures: DB, Redis, Kafka, JWT helpers, HTTP client
  factories.py             # User/document/session factory functions
  mock_embedding.py        # Deterministic mock embeddings (no API calls)
  e2e/
    test_document_lifecycle.py    # Upload → Ingest → Ready → Query → Delete
    test_chat_flow.py             # Chat → Agent → SSE response
    test_auth.py                  # JWT validation, unauthorized access
    test_rate_limiting.py         # Rate limit enforcement
    test_analytics.py             # Token/latency aggregation
    test_multi_user_isolation.py  # RLS, cross-user data isolation
    test_error_handling.py        # Invalid inputs, service failures
```

---

## Shared Fixtures (`conftest.py`)

### Key fixtures to build:
| Fixture | Scope | Purpose |
|---------|-------|---------|
| `db_pool` | session | Async SQLAlchemy engine + init schema from `init.sql` |
| `redis_client` | session | aioredis client, flush between tests |
| `kafka_producer` / `kafka_consumer` | session | aiokafka clients for topic inspection |
| `http_client` | function | httpx.AsyncClient pointed at test backend |
| `test_user` | function | Insert user row, return User + JWT token |
| `test_user_b` | function | Second user for isolation tests |
| `auth_header(user)` | helper | `{"Authorization": "Bearer <jwt>"}` |
| `upload_file(client, token, filename, content)` | helper | Upload and return doc_id |
| `wait_for_document_status(doc_id, status, timeout)` | helper | Poll until document reaches target status |
| `collect_sse_events(response)` | helper | Parse SSE stream into list of events |

### `factories.py`
```python
def create_user(db, email, name) -> User
def create_jwt(user_id, secret) -> str
def create_document_record(db, user, filename, status) -> Document
def create_session_with_messages(db, user, messages) -> Session
def sample_pdf_bytes() -> bytes      # minimal valid PDF
def sample_docx_bytes() -> bytes     # minimal valid DOCX
def sample_txt_content() -> bytes    # plain text
```

### `mock_embedding.py`
- Deterministic embedding function returning fixed 1024-dim vectors
- Patched into worker's embedding module during tests to avoid Voyage/Cohere API calls

---

## E2E Test Scenarios

### 1. Document Lifecycle (`test_document_lifecycle.py`)

**Flow**: Upload → Worker Ingestion → Status Ready → List → Delete

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 1.1 | Upload PDF | POST `/api/documents/` with valid PDF | 201, `status: "processing"`, doc_id returned |
| 1.2 | Upload DOCX | POST with valid DOCX | 201, correct file_type |
| 1.3 | Upload TXT | POST with plain text file | 201, correct file_type |
| 1.4 | Upload MD | POST with markdown file | 201, correct file_type |
| 1.5 | Reject unsupported type | POST with `.exe` file | 422, error message |
| 1.6 | Reject empty file | POST with 0-byte file | 422, "file is empty" |
| 1.7 | Reject oversized file | POST with file > MAX_UPLOAD_SIZE_MB | 413, size error |
| 1.8 | Ingestion completes | Upload PDF, poll status | Status transitions: `processing` → `ready` |
| 1.9 | Chunks created | After ingestion, query chunks table | Chunks exist with embeddings, correct doc_id/user_id |
| 1.10 | List documents | GET `/api/documents/` | Returns uploaded docs, paginated |
| 1.11 | List pagination | Upload 25 docs, request page=2&per_page=10 | Returns docs 11-20 |
| 1.12 | Delete document | DELETE `/api/documents/{id}` | 200, document removed from DB |
| 1.13 | Delete cleans chunks | After delete, query chunks table | No chunks for deleted doc_id |
| 1.14 | Delete nonexistent | DELETE with random UUID | 404 |
| 1.15 | Delete invalid UUID | DELETE with "not-a-uuid" | 422 |

### 2. Chat Flow (`test_chat_flow.py`)

**Flow**: POST chat → Kafka → Agent → Redis → SSE response

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 2.1 | New session chat | POST `/api/chat` with message, no session_id | SSE stream with `data:` events and `event: done` |
| 2.2 | Session created | After chat, GET `/api/chat/sessions` | New session appears |
| 2.3 | Message persisted | After chat, GET `/api/chat/history?session_id=X` | User message in history |
| 2.4 | Continue session | POST `/api/chat` with existing session_id | Works, same session |
| 2.5 | Invalid session_id | POST with nonexistent session_id | 404 |
| 2.6 | Other user's session | User B tries User A's session_id | 404 |
| 2.7 | Chat history limit | Create 200 messages, GET with limit=50 | Returns exactly 50 |
| 2.8 | SSE stream format | Inspect raw SSE events | Correct `data:`, `event:`, keepalive format |
| 2.9 | Chat with retrieval | Upload doc, wait ready, ask about its content | Response references document content |

### 3. Authentication (`test_auth.py`)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 3.1 | No token | Request without Authorization header | 401/403 |
| 3.2 | Invalid JWT | Request with malformed JWT | 401 |
| 3.3 | Expired JWT | Request with expired JWT | 401 |
| 3.4 | Wrong secret | JWT signed with different secret | 401 |
| 3.5 | Nonexistent user | Valid JWT but user_id not in DB | 401 "User not found" |
| 3.6 | Valid token | Request with valid JWT | 200, authorized |
| 3.7 | Token exchange | POST `/api/internal/token-exchange` with valid token | Returns DB credentials |
| 3.8 | Token replay | Exchange same token twice | Second call fails (consumed) |
| 3.9 | Token expiry | Exchange token after 5+ minutes | Fails |

### 4. Rate Limiting (`test_rate_limiting.py`)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 4.1 | Under limit | Send 5 requests | All succeed |
| 4.2 | At limit | Send 30 requests within 1 minute | All succeed |
| 4.3 | Over limit | Send 31 requests within 1 minute | 31st returns 429 |
| 4.4 | Per-user isolation | User A at limit, User B sends request | User B succeeds |
| 4.5 | Window reset | Hit limit, wait for window to pass, send again | Succeeds |

### 5. Analytics (`test_analytics.py`)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 5.1 | Empty analytics | New user, GET `/api/analytics/` | total_queries=0, total_tokens=0 |
| 5.2 | With messages | Seed assistant messages with metadata | Correct token count, latency avg |
| 5.3 | Date filtering | Seed messages across dates, filter with from/to | Only matching messages counted |
| 5.4 | Cost estimate | Seed known token counts | Correct USD calculation |
| 5.5 | Cross-user isolation | User A has messages, User B queries analytics | User B sees 0 |

### 6. Multi-User Isolation (`test_multi_user_isolation.py`)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 6.1 | Document isolation | User A uploads doc, User B lists docs | User B sees empty list |
| 6.2 | Delete isolation | User A uploads doc, User B tries to delete | 404 |
| 6.3 | Session isolation | User A creates session, User B lists sessions | User B sees empty list |
| 6.4 | History isolation | User A has chat history, User B queries it | 404 |
| 6.5 | Chunk RLS | User A ingests doc, User B queries chunks via scoped role | No rows returned |

### 7. Error Handling (`test_error_handling.py`)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 7.1 | Malformed JSON body | POST `/api/chat` with invalid JSON | 422 |
| 7.2 | Missing required field | POST `/api/chat` without `message` | 422 |
| 7.3 | Ingestion failure | Upload corrupted PDF | Document status → `failed` |
| 7.4 | Agent error recovery | Trigger agent error, check Redis | `error:` event followed by `[DONE]` |

---

## Critical Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `ai-project/tests/conftest.py` | Create | Shared fixtures, DB setup, HTTP client |
| `ai-project/tests/factories.py` | Create | Test data factories |
| `ai-project/tests/mock_embedding.py` | Create | Mock embedding provider |
| `ai-project/tests/e2e/test_document_lifecycle.py` | Create | Document CRUD + ingestion tests |
| `ai-project/tests/e2e/test_chat_flow.py` | Create | Chat → SSE flow tests |
| `ai-project/tests/e2e/test_auth.py` | Create | Authentication tests |
| `ai-project/tests/e2e/test_rate_limiting.py` | Create | Rate limit tests |
| `ai-project/tests/e2e/test_analytics.py` | Create | Analytics endpoint tests |
| `ai-project/tests/e2e/test_multi_user_isolation.py` | Create | Cross-user isolation tests |
| `ai-project/tests/e2e/test_error_handling.py` | Create | Error scenario tests |
| `ai-project/tests/e2e/__init__.py` | Create | Package marker |
| `ai-project/tests/__init__.py` | Create | Package marker |
| `ai-project/docker-compose.test.yml` | Create | Test infrastructure overlay |
| `ai-project/requirements-test.txt` | Create | Test dependencies |
| `ai-project/pytest.ini` | Create | Pytest configuration |

---

## Test Dependencies (`requirements-test.txt`)

```
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
factory-boy>=3.3
faker>=24.0
```

---

## Verification

1. **Start test infrastructure**: `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d`
2. **Run full suite**: `pytest tests/e2e/ -v --tb=short`
3. **Run specific scenario**: `pytest tests/e2e/test_document_lifecycle.py -v`
4. **Check coverage**: `pytest tests/e2e/ --cov=backend --cov=worker --cov=agent`
5. **Teardown**: `docker compose -f docker-compose.yml -f docker-compose.test.yml down -v`
