# API Flows — Chat

---

## POST /api/chat

```
Client
  │
  └─ POST /api/chat
      body: { message, session_id? }
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: rate-limit check (sliding window, Redis)
      │     key: ratelimit:{user_id}
      │     → 429 Too Many Requests if exceeded
      │
      ├─ backend: resolve or create session
      │     session_id provided → SELECT sessions WHERE id=? AND user_id=<jwt.sub>
      │                           → 404 if not found or owned by another user
      │     session_id null     → INSERT sessions (user_id)  → new session_id
      │
      ├─ backend: persist user message
      │     INSERT messages (session_id, role='user', content=message)
      │
      ├─ backend: generate one-time token
      │     token = "otk_" + secrets.token_urlsafe(32)
      │     INSERT tokens (token, user_id, created_at=now)
      │     [expires in 5 minutes; single use]
      │
      ├─ backend: publish to Kafka
      │     kafka_producer.send("chat.query", {
      │       session_id, user_id, message, token
      │     })
      │     → 503 Service Unavailable if Kafka publish fails
      │
      └─ backend: return StreamingResponse (SSE, text/event-stream)
           SUBSCRIBE Redis pub/sub channel  session:{session_id}
           headers: Cache-Control: no-cache, X-Accel-Buffering: no
                │
                │  SSE relay loop (timeout: 120 s with no message)
                │
                │    Redis message "chunk:<text>"  →  data: <text>\n\n
                │    Redis message "error:<msg>"   →  event: error\ndata: <msg>\n\n  → close
                │    Redis message "[DONE]"        →  event: done\ndata: [DONE]\n\n  → close
                │    no message yet               →  : keepalive\n\n  (every ~100 ms)
                │    timeout (120 s)              →  event: error\ndata: Stream timed out → close
                │
                │ (async, parallel — Kafka consumer)
                ▼
           Agent (chat.query)
             │
             ├─ POST /api/internal/token-exchange
             │     body: { token }
             │     backend: UPDATE tokens SET consumed_at=now
             │              WHERE token=? AND consumed_at IS NULL AND created_at > now-300s
             │              RETURNING user_id
             │     → 401 if invalid, expired, or already consumed  (token burned on first use)
             │     backend: SELECT users.db_role WHERE id=user_id
             │     backend: CREATE/RESET ROLE {db_role} PASSWORD '<new-random>'
             │     response: { db_user, db_password, db_host, db_port, db_name }
             │
             ├─ build scoped db_url from credentials
             │     injected into tool handlers via Python closures
             │     LLM never sees db_user or db_password
             │
             ├─ connect to PostgreSQL as user's scoped role
             │     RLS policies enforce per-user data isolation at DB level
             │     chunks and documents filtered to this user's rows automatically
             │
             ├─ policy engine: validate mode = "kafka"
             │     allowed tools: hybrid_retrieval, query_database, analyze_csv,
             │                    generate_chart, extract_pdf, extract_doc, TodoWrite, load_skill
             │     denied tools:  bash, background_run, write_file, edit_file
             │     limits:        50 tool calls/session, 20 tool calls/min
             │
             ├─ scan message for prompt injection
             │     patterns: "ignore all previous instructions", role hijacking,
             │               tool manipulation, data exfiltration, hidden markers
             │     → flagged matches logged as warnings (soft block)
             │
             ├─ ReAct loop (Anthropic claude-sonnet-4-6 or OpenAI gpt-4o)
             │    │
             │    ├─ Reason: LLM reads message + history → decides next action
             │    │
             │    ├─ Act: policy engine validates tool call, then invokes handler
             │    │    │
             │    │    ├─ hybrid_retrieval(query, top_k)
             │    │    │     embed query → pgvector cosine ANN search
             │    │    │     +  pg_trgm BM25 text search
             │    │    │     → RRF fusion (k=60) → top_k chunks
             │    │    │     → scan retrieved chunks for injection
             │    │    │     → return formatted results to LLM
             │    │    │
             │    │    ├─ query_database(sql)
             │    │    │     validate: must be SELECT, deny DDL/DML patterns
             │    │    │     execute against PostgreSQL (scoped role, RLS active)
             │    │    │     → return rows as text
             │    │    │
             │    │    ├─ get_stock_price(symbol)
             │    │    │     yfinance → live price + info
             │    │    │
             │    │    ├─ analyze_csv(file_path, query?)
             │    │    │     pandas read_csv → shape/dtypes/describe
             │    │    │     optional df.eval(query) for expressions
             │    │    │
             │    │    ├─ generate_chart(data, chart_type, title, output_path)
             │    │    │     matplotlib → bar/line/pie PNG
             │    │    │
             │    │    ├─ extract_pdf(file_path)  → pdfplumber
             │    │    └─ extract_doc(file_path)  → python-docx
             │    │
             │    └─ Observe: tool result appended to message history → back to Reason
             │         loop continues until LLM emits final text (no more tool calls)
             │
             ├─ stream response chunks via Redis pub/sub
             │     PUBLISH session:{session_id}  "chunk:<text>"   (per LLM token/chunk)
             │     backend SSE relay picks these up and forwards to client
             │
             ├─ persist assistant message
             │     INSERT messages (session_id, role='assistant', content=full_response,
             │       metadata={ token_count, latency_ms, model, provider })
             │
             └─ PUBLISH session:{session_id}  "[DONE]"
                  backend SSE relay closes the stream
```

---

## GET /api/chat/history

```
Client
  │
  └─ GET /api/chat/history?session_id=<id>&limit=100
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: verify session ownership
      │     SELECT sessions WHERE id=session_id AND user_id=<jwt.sub>
      │     → 404 if not found or owned by another user
      │
      ├─ backend: fetch messages
      │     SELECT messages WHERE session_id=?
      │     ORDER BY created_at ASC
      │     LIMIT limit (max 500)
      │
      └─ response 200 OK
            {
              session_id,
              messages: [{ id, role, content, metadata, created_at }]
            }
```

---

## GET /api/chat/sessions

```
Client
  │
  └─ GET /api/chat/sessions
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: list sessions
      │     SELECT sessions WHERE user_id=<jwt.sub>
      │     ORDER BY updated_at DESC
      │
      └─ response 200 OK
            {
              sessions: [{ session_id, title, created_at, updated_at }]
            }
```
