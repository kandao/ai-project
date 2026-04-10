# Backend PRD — FastAPI Application

> The HTTP gateway for the entire system. Handles client requests, publishes to Kafka, relays agent responses via SSE, and manages per-user database credentials. Does not call the LLM directly — all intelligence is delegated to the agent via Kafka.

---

## Purpose

The backend is a stateless FastAPI service that sits between the client and the async processing pipeline. It:

1. Accepts document uploads and publishes ingestion jobs to Kafka
2. Accepts chat queries, generates one-time tokens, and publishes to Kafka
3. Relays agent responses from Redis pub/sub to the client via SSE
4. Manages per-user PostgreSQL roles and one-time token lifecycle
5. Serves document and session metadata from PostgreSQL

---

## File Structure

```
backend/
  ├── main.py                       # FastAPI app, middleware, startup/shutdown
  ├── config.py                     # Settings from env vars (pydantic-settings)
  ├── routers/
  │   ├── documents.py              # upload, list, delete
  │   ├── chat.py                   # chat endpoint + SSE relay
  │   ├── analytics.py              # token usage, cost, latency
  │   └── internal.py               # token-exchange (agent-only, internal network)
  ├── services/
  │   ├── kafka_producer.py         # publish to doc.ingest and chat.query topics
  │   ├── token_manager.py          # one-time token generation, validation, cleanup
  │   ├── db_role_manager.py        # create/drop per-user PostgreSQL roles
  │   ├── file_storage.py           # S3 or local file storage abstraction
  │   └── session.py                # session metadata CRUD
  ├── models/
  │   ├── document.py               # SQLAlchemy: documents table
  │   ├── session.py                # SQLAlchemy: sessions + messages table
  │   ├── token.py                  # SQLAlchemy: one-time tokens table
  │   └── user.py                   # SQLAlchemy: users + db_roles table
  ├── middleware/
  │   ├── auth.py                   # JWT verification
  │   └── rate_limit.py             # Redis-based rate limiting
  ├── database.py                   # SQLAlchemy engine, session factory
  ├── Dockerfile
  ├── requirements.txt
  └── .env.example
```

---

## Environment Variables

```bash
# Core
DATABASE_URL=postgresql://admin:pass@postgres:5432/docqa
REDIS_URL=redis://redis:6379
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# Auth
JWT_SECRET=...
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=60

# File storage
STORAGE_BACKEND=local                 # or "s3"
STORAGE_LOCAL_PATH=/data/uploads
S3_BUCKET=docqa-uploads               # if STORAGE_BACKEND=s3
AWS_ACCESS_KEY_ID=...                 # if STORAGE_BACKEND=s3
AWS_SECRET_ACCESS_KEY=...             # if STORAGE_BACKEND=s3

# Limits
MAX_UPLOAD_SIZE_MB=50
RATE_LIMIT_REQUESTS_PER_MINUTE=30
```

---

## API Endpoints

### Documents

```
POST   /api/documents/upload
  - multipart/form-data: file (PDF, DOCX, TXT, MD)
  - Validates file type and size (MAX_UPLOAD_SIZE_MB)
  - Stores file to S3 or local filesystem
  - Creates document record in PostgreSQL
  - Publishes to Kafka: doc.ingest { file_path, doc_id, user_id }
  - Returns: { doc_id, filename, status: "processing" }

GET    /api/documents
  - Query params: ?page=1&per_page=20
  - Returns paginated list of user's documents with status (processing/ready/failed)

DELETE /api/documents/{doc_id}
  - Deletes document record, stored file, and all associated chunks/vectors
  - Publishes to Kafka: doc.delete { doc_id } (worker cleans up vectors)
```

### Chat

```
POST   /api/chat
  - Body: { "message": "...", "session_id": "..." }
  - If session_id is omitted, creates a new session
  - Steps:
      1. Validate JWT → extract user_id
      2. Generate one-time token (maps to user's DB role)
      3. Store user message in session history
      4. Publish to Kafka: chat.query { message, session_id, token }
      5. Subscribe to Redis: session:{session_id}
      6. Stream response back via SSE
  - Returns: SSE stream (text/event-stream)

GET    /api/chat/history
  - Query params: ?session_id=...
  - Returns message history for the session

GET    /api/chat/sessions
  - Returns list of user's chat sessions with last message preview
```

### Analytics

```
GET    /api/analytics
  - Query params: ?from=...&to=...
  - Returns: token usage, cost per query, average latency
  - Scoped to authenticated user (admin sees all)
```

### Internal (agent-only)

```
POST   /api/internal/token-exchange
  - Body: { "token": "otk_..." }
  - Not exposed to public network (Docker internal only)
  - Steps:
      1. Look up token in database
      2. Validate: not expired, not consumed
      3. Mark token as consumed
      4. Look up user's PostgreSQL role
      5. Return scoped DB credentials
  - Response (200): { db_user, db_password, db_host, db_port, db_name }
  - Response (401): token invalid, expired, or already consumed
  - Response (404): user/role not found
```

---

## SSE Relay — Redis to Client

The backend does not generate the response. It subscribes to a Redis pub/sub channel and forwards messages as SSE events.

### Flow

```
1. POST /api/chat received
2. Backend publishes to Kafka: chat.query
3. Backend subscribes to Redis channel: session:{session_id}
4. Return StreamingResponse (text/event-stream)
5. For each Redis message:
     "chunk:<text>"  → SSE: data: <text>\n\n
     "error:<msg>"   → SSE: event: error\ndata: <msg>\n\n
     "[DONE]"        → SSE: event: done\ndata: [DONE]\n\n  → close stream
```

### Implementation

```python
# routers/chat.py

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/chat")

@router.post("")
async def chat(request: ChatRequest, user=Depends(get_current_user)):
    session_id = request.session_id or create_session(user.id)
    token = token_manager.create(user.id)

    # Publish to Kafka
    await kafka_producer.send(
        "chat.query",
        {
            "message": request.message,
            "session_id": session_id,
            "token": token,
        },
    )

    # SSE relay
    return StreamingResponse(
        redis_stream(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # disable nginx buffering
        },
    )


async def redis_stream(session_id: str):
    """Subscribe to Redis and yield SSE events."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"session:{session_id}")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = message["data"].decode()

            if data.startswith("chunk:"):
                yield f"data: {data[6:]}\n\n"
            elif data.startswith("error:"):
                yield f"event: error\ndata: {data[6:]}\n\n"
            elif data == "[DONE]":
                yield f"event: done\ndata: [DONE]\n\n"
                break
    finally:
        await pubsub.unsubscribe(f"session:{session_id}")
```

### Timeout

If no message arrives on the Redis channel within 120 seconds, the backend closes the SSE stream and publishes a timeout error. This prevents dangling connections if the agent crashes.

---

## Kafka Publishing

### Topics

| Topic | Publisher | Consumer | Payload |
|---|---|---|---|
| `chat.query` | Backend | Agent | `{ message, session_id, token }` |
| `doc.ingest` | Backend | Ingestion Worker | `{ file_path, doc_id, user_id }` |
| `doc.delete` | Backend | Ingestion Worker | `{ doc_id }` |

### Producer

```python
# services/kafka_producer.py

from aiokafka import AIOKafkaProducer

class KafkaProducerService:
    def __init__(self, bootstrap_servers: str):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

    async def start(self):
        await self.producer.start()

    async def stop(self):
        await self.producer.stop()

    async def send(self, topic: str, payload: dict):
        await self.producer.send_and_wait(topic, payload)
```

Started on FastAPI `lifespan` startup, stopped on shutdown.

---

## One-Time Token Management

### Token Lifecycle

```
1. Backend generates token on POST /api/chat
2. Token stored in PostgreSQL: { token_id, user_id, created_at, consumed_at }
3. Published in Kafka message alongside chat query
4. Agent calls POST /api/internal/token-exchange
5. Backend validates + marks as consumed (atomic operation)
6. Backend returns scoped DB credentials
7. Cleanup job deletes tokens older than 1 hour
```

### Database Schema

```sql
CREATE TABLE tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token       VARCHAR(64) UNIQUE NOT NULL,
    user_id     UUID NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ,
    expired     BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX idx_tokens_token ON tokens (token) WHERE consumed_at IS NULL;
```

### Token Generation

```python
# services/token_manager.py

import secrets

TOKEN_PREFIX = "otk_"
TOKEN_EXPIRY_SECONDS = 300  # 5 minutes

class TokenManager:
    def create(self, user_id: str) -> str:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        # INSERT into tokens table
        return token

    def exchange(self, token: str) -> dict:
        """
        Atomic: SELECT ... FOR UPDATE + mark consumed.
        Returns user's DB credentials or raises.
        """
        row = db.execute(
            "UPDATE tokens SET consumed_at = now() "
            "WHERE token = :token AND consumed_at IS NULL "
            "AND created_at > now() - interval ':expiry seconds' "
            "RETURNING user_id",
            {"token": token, "expiry": TOKEN_EXPIRY_SECONDS},
        ).fetchone()

        if not row:
            raise InvalidTokenError()

        return self.get_user_db_credentials(row.user_id)
```

---

## Per-User PostgreSQL Role Management

Each user gets a dedicated PostgreSQL role with permissions scoped to their data. This is the foundation of per-user data isolation.

### How It Works

```
1. User registers → backend creates a PostgreSQL role
2. Role has SELECT on chunks, documents tables
3. Row-level security (RLS) policies restrict access to rows where user_id matches
4. On token exchange, backend returns this role's credentials
5. Agent connects to PostgreSQL as this role → can only see user's data
```

### Database Setup

```sql
-- Enable RLS on data tables
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

-- Policy: users can only see their own data
CREATE POLICY user_isolation_chunks ON chunks
    FOR SELECT USING (metadata->>'user_id' = current_user);

CREATE POLICY user_isolation_documents ON documents
    FOR SELECT USING (user_id::text = current_user);
```

### Role Lifecycle

```python
# services/db_role_manager.py

class DBRoleManager:
    def create_role(self, user_id: str) -> dict:
        """Create a scoped PostgreSQL role for a user."""
        role_name = f"user_{user_id[:8]}"
        password = secrets.token_urlsafe(24)

        # Create role with limited permissions
        db.execute(f"CREATE ROLE {role_name} LOGIN PASSWORD '{password}'")
        db.execute(f"GRANT SELECT ON chunks, documents TO {role_name}")
        db.execute(f"GRANT USAGE ON SCHEMA public TO {role_name}")

        # Store credentials (encrypted)
        # ...
        return {"db_user": role_name, "db_password": password}

    def drop_role(self, user_id: str):
        """Remove PostgreSQL role when user is deleted."""
        role_name = f"user_{user_id[:8]}"
        db.execute(f"DROP ROLE IF EXISTS {role_name}")
```

---

## JWT Authentication

All public endpoints require a valid JWT in the `Authorization: Bearer <token>` header.

### Middleware

```python
# middleware/auth.py

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = payload["sub"]
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
```

### Internal Endpoints

`/api/internal/*` routes skip JWT auth. They are protected by network isolation — only accessible within the Docker network.

```python
# main.py

app = FastAPI()

# Public routes — JWT required
app.include_router(documents.router, dependencies=[Depends(get_current_user)])
app.include_router(chat.router, dependencies=[Depends(get_current_user)])
app.include_router(analytics.router, dependencies=[Depends(get_current_user)])

# Internal routes — no JWT, network-isolated
app.include_router(internal.router)
```

---

## Rate Limiting

Redis-based sliding window rate limiter. Applied per user.

```python
# middleware/rate_limit.py

class RateLimiter:
    def __init__(self, redis_client, max_requests: int, window_seconds: int):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window = window_seconds

    async def check(self, user_id: str) -> bool:
        key = f"ratelimit:{user_id}"
        now = time.time()

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - self.window)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, self.window)
        results = await pipe.execute()

        count = results[2]
        return count <= self.max_requests
```

Applied to `/api/chat` and `/api/documents/upload` endpoints.

---

## Database Models

### Users

```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255) UNIQUE NOT NULL,
    name        VARCHAR(255),
    db_role     VARCHAR(64),              -- PostgreSQL role name
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Documents

```sql
CREATE TABLE documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    filename    VARCHAR(255) NOT NULL,
    file_path   TEXT NOT NULL,
    file_type   VARCHAR(10) NOT NULL,     -- pdf, docx, txt, md
    file_size   INTEGER NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'processing',  -- processing/ready/failed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Sessions & Messages

```sql
CREATE TABLE sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    title       VARCHAR(255),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id          SERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions(id),
    role        VARCHAR(10) NOT NULL,     -- user/assistant
    content     TEXT NOT NULL,
    metadata    JSONB,                    -- token_count, latency_ms, model, provider
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Startup / Shutdown

```python
# main.py

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await kafka_producer.start()
    await redis_pool.connect()
    await db_engine.connect()
    yield
    # Shutdown
    await kafka_producer.stop()
    await redis_pool.disconnect()
    await db_engine.dispose()

app = FastAPI(lifespan=lifespan)
```

---

## Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose entry

```yaml
backend:
  build: ./backend
  ports:
    - "8000:8000"
  environment:
    - DATABASE_URL=postgresql://admin:pass@postgres:5432/docqa
    - REDIS_URL=redis://redis:6379
    - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    - JWT_SECRET=${JWT_SECRET}
    - STORAGE_BACKEND=local
    - STORAGE_LOCAL_PATH=/data/uploads
  volumes:
    - upload-data:/data/uploads
  depends_on:
    - postgres
    - kafka
    - redis
```

---

## Dependencies

```
fastapi
uvicorn[standard]
python-multipart
sqlalchemy[asyncio]
asyncpg
psycopg2-binary
aiokafka
redis[hiredis]
PyJWT
pydantic-settings
python-dotenv
boto3                    # if S3 storage
```

---

## Out of Scope (v1)

- User registration / login UI (assumes external identity provider or direct DB seeding)
- SSO / SAML / OAuth2 provider integration
- WebSocket transport (SSE is sufficient for unidirectional streaming)
- Horizontal scaling of SSE connections (single instance per session is acceptable for v1)
- API versioning
- OpenAPI documentation customization
