# API Flows — Analytics and Health

---

## GET /api/analytics/

```
Client
  │
  └─ GET /api/analytics/?from=<iso_date>&to=<iso_date>
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: subquery — user's sessions
      │     SELECT session.id WHERE session.user_id = <jwt.sub>
      │
      ├─ backend: fetch assistant messages
      │     SELECT messages
      │     WHERE session_id IN (user's sessions)
      │       AND role = 'assistant'
      │       AND created_at >= from_date   [optional]
      │       AND created_at <= to_date     [optional]
      │
      ├─ backend: aggregate from messages.metadata JSONB
      │     total_queries = COUNT(*)
      │     total_tokens  = SUM(metadata->>'token_count')
      │     avg_latency   = AVG(metadata->>'latency_ms')
      │     cost_estimate = total_tokens / 1000 * 0.003  (USD estimate)
      │
      └─ response 200 OK
            {
              total_queries,
              total_tokens,
              avg_latency_ms,
              cost_estimate,
              cost_estimate_currency: "USD"
            }
```

---

## GET /health

```
Client
  │
  └─ GET /health
      [no authentication required]
      │
      └─ response 200 OK
            { status: "ok" }
```

---

## POST /api/internal/token-exchange

Agent-only. Not accessible from the public internet — protected by Docker network isolation only (no JWT check).

```
Agent
  │
  └─ POST /api/internal/token-exchange
      body: { token }
      [internal Docker network — no Authorization header]
      │
      ├─ backend: atomic consume
      │     UPDATE tokens
      │     SET consumed_at = now()
      │     WHERE token = ?
      │       AND consumed_at IS NULL          ← single-use guard
      │       AND created_at > now() - 300s    ← 5-minute expiry
      │     RETURNING user_id
      │     → 401 Unauthorized if no row matched
      │       (token invalid, already consumed, or expired)
      │
      ├─ backend: look up user's DB role
      │     SELECT users.db_role WHERE id = user_id
      │     → 404 if user or role not found
      │
      ├─ backend: provision / reset PostgreSQL role
      │     CREATE ROLE {db_role} LOGIN PASSWORD '<new-random>'
      │     [idempotent: resets password if role already exists]
      │
      ├─ backend: parse connection info from DATABASE_URL
      │     host, port, db_name extracted from the env var
      │
      └─ response 200 OK
            {
              db_user,      ← PostgreSQL role name, e.g. "user_abc12345"
              db_password,  ← fresh random password (reset on every exchange)
              db_host,
              db_port,
              db_name
            }

  Agent receives credentials and:
    ├─ builds db_url string in Python (never sent to LLM)
    ├─ injects into tool handlers via closures
    └─ connects to PostgreSQL as {db_user}
         RLS policies kick in automatically:
           chunks and documents filtered to this user's rows
```
