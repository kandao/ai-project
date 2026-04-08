# Agent PRD — Agent03 (Per-User Access Control)

> Builds on Agent02. Adds a one-time token exchange mechanism so the agent operates with per-user database credentials. The LLM never sees the credentials — they exist only in Python runtime.

---

## Purpose

Prevent data leakage between users. Each Kafka message carries a one-time token. Before the ReAct loop starts, the agent exchanges this token with the backend API for a scoped PostgreSQL user credential. All database tools use this credential for the duration of the agent lifecycle.

---

## Scope

| Item | File | Description |
|---|---|---|
| Token exchange client | `auth.py` | NEW — Python-level HTTP call to backend, returns DB credentials |
| Scoped DB connection | `tools/retrieval.py`, `tools/database.py` | MODIFY — accept credential at runtime instead of global env |
| Consumer integration | `consumer.py` | MODIFY — exchange token before loop, inject credential into tool context |
| Architecture update | `Architecture.md` | MODIFY — document internal API exception and token flow |

**CLI mode**: not supported. No backend to exchange tokens with. CLI continues to use `DATABASE_URL` from `.env` as before.

---

## Flow

```
1. User sends chat via frontend
2. Backend:
   a. Validates user session
   b. Generates one-time token (maps to the user's DB role)
   c. Publishes to Kafka: chat.query { message, session_id, token }
3. Agent (consumer.py):
   a. Consumes message, extracts token
   b. Calls POST /api/internal/token-exchange { token }
      ← receives { db_user, db_password, db_host, db_name }
   c. Builds scoped DB connection string (Python only — never in LLM context)
   d. Injects credential into tool handlers
   e. Runs agent_loop() — LLM calls tools normally, tools use scoped credential
4. Agent finishes, credential stays in memory for reuse within session
```

---

## File Changes

```
agent/
  ├── auth.py                   # NEW — token exchange client
  ├── consumer.py               # MODIFY — call auth before loop
  └── tools/
      ├── retrieval.py          # MODIFY — accept db_url parameter
      └── database.py           # MODIFY — accept db_url parameter
```

---

## auth.py — Token Exchange Client

Pure Python utility. Not an LLM tool — the LLM never knows this exists.

```python
# auth.py

import os
import requests

BACKEND_URL = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8000")

def exchange_token(token: str) -> dict:
    """
    Exchange a one-time token for scoped DB credentials.

    Args:
        token: One-time token from Kafka message

    Returns:
        {
            "db_user": "user_abc123",
            "db_password": "...",
            "db_host": "postgres",
            "db_port": 5432,
            "db_name": "docqa"
        }

    Raises:
        AuthError: token is invalid, expired, or already used
    """
    resp = requests.post(
        f"{BACKEND_URL}/api/internal/token-exchange",
        json={"token": token},
        timeout=5,
    )
    if resp.status_code != 200:
        raise AuthError(f"Token exchange failed: {resp.status_code} {resp.text}")
    return resp.json()


def build_db_url(creds: dict) -> str:
    """Build PostgreSQL connection string from credentials."""
    return (
        f"postgresql://{creds['db_user']}:{creds['db_password']}"
        f"@{creds['db_host']}:{creds.get('db_port', 5432)}"
        f"/{creds['db_name']}"
    )


class AuthError(Exception):
    pass
```

---

## Kafka Message Schema (updated)

```json
{
  "message": "what does the refund policy say?",
  "session_id": "abc-123",
  "token": "otk_a1b2c3d4e5f6"
}
```

The `token` field is added by the backend. It is:
- Single-use: the backend marks it as consumed on exchange
- Short-lived: expires if not exchanged within a reasonable window
- Mapped to a specific PostgreSQL user role on the backend side

---

## consumer.py — Integration

```python
# In consumer.py message handler (before agent_loop)

from auth import exchange_token, build_db_url, AuthError

def handle_message(msg):
    payload = json.loads(msg.value)
    session_id = payload["session_id"]
    token = payload.get("token")

    if not token:
        publish_error(session_id, "Missing access token")
        return

    # Step 1: Exchange token for DB credentials (Python only)
    try:
        creds = exchange_token(token)
        db_url = build_db_url(creds)
    except AuthError as e:
        publish_error(session_id, f"Authentication failed: {e}")
        return

    # Step 2: Inject scoped credential into tool handlers
    #   db_url is passed to tool factories — never to the LLM
    tools, handlers = build_scoped_tools(db_url)

    # Step 3: Run the loop with scoped tools
    messages = [{"role": "user", "content": payload["message"]}]
    agent_loop(messages, tools=tools, handlers=handlers)
    stream_to_redis(session_id, messages)
```

---

## Tool Modifications

### tools/retrieval.py

Currently uses global `DATABASE_URL`. Agent03 changes it to accept a connection string at call time:

```python
# Before (Agent02):
def hybrid_retrieval(query: str, top_k: int = 5) -> str:
    db_url = os.getenv("DATABASE_URL")
    ...

# After (Agent03):
def hybrid_retrieval(query: str, top_k: int = 5, db_url: str = None) -> str:
    db_url = db_url or os.getenv("DATABASE_URL")
    ...
```

The `db_url` parameter is injected by the handler wrapper in `consumer.py`, not by the LLM:

```python
def build_scoped_tools(db_url: str):
    """Build tool handlers with scoped DB credential baked in."""
    scoped_handlers = {
        **ALL_HANDLERS,  # base handlers unchanged
        "hybrid_retrieval": lambda **kw: hybrid_retrieval(
            kw["query"], kw.get("top_k", 5), db_url=db_url
        ),
        "query_database": lambda **kw: query_database(
            kw["sql"], db_url=db_url
        ),
    }
    return ALL_TOOLS, scoped_handlers
```

The LLM tool schema stays the same — `hybrid_retrieval(query, top_k)` and `query_database(sql)`. The LLM has no visibility into the `db_url` parameter.

### tools/database.py

Same pattern:

```python
# Before:
def query_database(sql: str) -> str:
    # uses local SQLite

# After (Kafka mode with credentials):
def query_database(sql: str, db_url: str = None) -> str:
    if db_url:
        # use PostgreSQL with scoped credential
    else:
        # fallback to local SQLite (CLI mode)
```

---

## loop.py — Minimal Change

`agent_loop()` gains optional `tools` and `handlers` parameters so `consumer.py` can inject scoped tools:

```python
# Before:
def agent_loop(messages: list):
    # uses global ALL_TOOLS, ALL_HANDLERS

# After:
def agent_loop(messages: list, tools: list = None, handlers: dict = None):
    tools = tools or ALL_TOOLS
    handlers = handlers or ALL_HANDLERS
    # rest unchanged
```

CLI mode calls `agent_loop(messages)` with no arguments — behavior unchanged.

---

## Security Constraints

| Rule | Enforcement |
|---|---|
| DB credentials never reach the LLM | `db_url` is injected via Python closure in handler wrappers, not in tool schemas or tool results |
| Token is single-use | Backend marks token as consumed on exchange; second exchange attempt returns 401 |
| Credential stays in Python runtime | No logging of `db_url` or `creds` dict; no serialization to transcript files |
| Tool results are safe | Query results are returned to the LLM (this is intentional — the scoped DB user can only access that user's data) |
| CLI mode unaffected | No token, no exchange — uses local SQLite or env `DATABASE_URL` |

### What NOT to log

`auto_compact()` saves transcripts to `.transcripts/`. The credential must not leak there. Since `db_url` only exists inside Python closures and never appears in the message list, this is enforced by design — no additional filtering needed.

---

## Backend API Contract

### POST /api/internal/token-exchange

**Request:**
```json
{
  "token": "otk_a1b2c3d4e5f6"
}
```

**Response (200):**
```json
{
  "db_user": "user_abc123",
  "db_password": "pg_scoped_pass_...",
  "db_host": "postgres",
  "db_port": 5432,
  "db_name": "docqa"
}
```

**Response (401):** token invalid, expired, or already consumed
**Response (404):** user does not exist in the system

This endpoint is internal-only — not exposed to the public internet. In Docker Compose, only the `agent` service can reach `backend:8000`.

---

## Environment Variables (additional)

```bash
BACKEND_INTERNAL_URL=http://backend:8000    # internal Docker network
```

---

## Dependencies (additional)

```
requests
```

---

## Out of Scope (Agent03)

- DB user creation/cleanup lifecycle (handled by backend)
- Token expiry window tuning
- Credential rotation within a session
- Password discarding after session ends (deferred to next iteration)
- Rate limiting on token-exchange endpoint (handled by backend)
- CLI mode access control
