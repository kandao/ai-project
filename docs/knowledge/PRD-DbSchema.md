# Database Schema

PostgreSQL 16 with the `vector` and `pg_trgm` extensions. Schema defined in `init.sql`.

---

## Tables

### `users`

Registered accounts. Each user is provisioned a dedicated PostgreSQL role on registration for row-level data isolation.

| Column            | Type          | Notes                                        |
|-------------------|---------------|----------------------------------------------|
| `id`              | UUID PK       | `gen_random_uuid()`                          |
| `email`           | VARCHAR(255)  | unique, not null                             |
| `name`            | VARCHAR(255)  | optional display name                        |
| `hashed_password` | VARCHAR(255)  | bcrypt hash                                  |
| `db_role`         | VARCHAR(64)   | PostgreSQL role name: `user_{uuid[:8]}`      |
| `created_at`      | TIMESTAMPTZ   | default `now()`                              |

Index: `idx_users_email` on `(email)`.

---

### `tokens`

Single-use tokens issued per chat request. The agent exchanges a token for scoped DB credentials without credentials ever passing through Kafka.

| Column        | Type        | Notes                                                    |
|---------------|-------------|----------------------------------------------------------|
| `id`          | UUID PK     |                                                          |
| `token`       | VARCHAR(64) | unique; format: `otk_<32-byte url-safe random>`          |
| `user_id`     | UUID FK     | references `users(id)` ON DELETE CASCADE                 |
| `created_at`  | TIMESTAMPTZ | token expires 5 minutes after this timestamp             |
| `consumed_at` | TIMESTAMPTZ | NULL = unused; set atomically on first exchange          |

Partial unique index `idx_tokens_token_unconsumed` on `(token) WHERE consumed_at IS NULL` — makes unconsumed-token lookups fast and enforces the single-use constraint at the DB level.

Exchange uses `UPDATE ... WHERE consumed_at IS NULL AND created_at > now() - 300s RETURNING user_id` — atomic check-and-consume with no race condition.

---

### `documents`

Metadata for every uploaded file. File bytes are stored separately (S3 or local disk).

| Column      | Type        | Notes                                          |
|-------------|-------------|------------------------------------------------|
| `id`        | UUID PK     |                                                |
| `user_id`   | UUID FK     | references `users(id)` ON DELETE CASCADE       |
| `filename`  | VARCHAR(255)| original filename as uploaded                  |
| `file_path` | TEXT        | S3 key or local filesystem path                |
| `file_type` | VARCHAR(10) | `pdf`, `docx`, `txt`, `md`                     |
| `file_size` | INTEGER     | bytes                                          |
| `status`    | VARCHAR(20) | `processing` → `ready` or `failed`             |
| `created_at`| TIMESTAMPTZ |                                                |

Index: `idx_documents_user_id` on `(user_id)`.

RLS policy `user_isolation_documents` restricts SELECT to rows where `user_id` matches the connected PostgreSQL role.

---

### `sessions`

Chat conversation containers. One session groups multiple messages into a thread.

| Column      | Type        | Notes                                          |
|-------------|-------------|------------------------------------------------|
| `id`        | UUID PK     |                                                |
| `user_id`   | UUID FK     | references `users(id)` ON DELETE CASCADE       |
| `title`     | VARCHAR(255)| optional label                                 |
| `created_at`| TIMESTAMPTZ |                                                |
| `updated_at`| TIMESTAMPTZ | bumped on each new message                     |

Index: `idx_sessions_user_id` on `(user_id)`.

---

### `messages`

Individual turns within a session. Both user messages and assistant responses are stored here.

| Column       | Type        | Notes                                                             |
|--------------|-------------|-------------------------------------------------------------------|
| `id`         | SERIAL PK   |                                                                   |
| `session_id` | UUID FK     | references `sessions(id)` ON DELETE CASCADE                       |
| `role`       | VARCHAR(10) | `user` or `assistant`                                             |
| `content`    | TEXT        | full message text                                                 |
| `metadata`   | JSONB       | `token_count`, `latency_ms`, `model`, `provider` — used by analytics |
| `created_at` | TIMESTAMPTZ |                                                                   |

Index: `idx_messages_session_id` on `(session_id)`.

---

### `chunks`

Text chunks produced by the ingestion worker. Each chunk stores a dense embedding for vector search and its text for BM25 search.

| Column        | Type          | Notes                                                        |
|---------------|---------------|--------------------------------------------------------------|
| `id`          | SERIAL PK     |                                                              |
| `doc_id`      | UUID FK       | references `documents(id)` ON DELETE CASCADE                 |
| `user_id`     | UUID          | denormalized from `documents.user_id` for fast RLS filtering |
| `content`     | TEXT          | raw chunk text (512 tokens by default)                       |
| `metadata`    | JSONB         | `chunk_index`, `source_filename`, language details           |
| `embedding`   | vector(1536)  | OpenAI `text-embedding-3-small` produces 1536 dimensions     |
| `language`    | VARCHAR(5)    | `en` or `ja` — determines chunking strategy used at ingest   |
| `chunk_index` | INTEGER       | sequential position within the source document               |
| `created_at`  | TIMESTAMPTZ   |                                                              |

Indexes:
- `idx_chunks_doc_id` on `(doc_id)`
- `idx_chunks_user_id` on `(user_id)`
- `idx_chunks_embedding_cosine` — IVFFlat cosine similarity (`lists=100`, suitable up to ~1M rows); used for ANN vector search via `embedding <=> query_vec`
- `idx_chunks_content_trgm` — GIN trigram (`gin_trgm_ops`); used for BM25-style full-text search via `to_tsvector @@ plainto_tsquery`

RLS policy `user_isolation_chunks` restricts SELECT to rows where `user_id` resolves to the connected PostgreSQL role.

---

## Row-Level Security (RLS)

RLS is enabled on `documents` and `chunks`. When the agent connects using scoped credentials (e.g. role `user_abc12345`), PostgreSQL automatically filters all rows:

```sql
CREATE POLICY user_isolation_documents ON documents FOR SELECT
USING (user_id IN (SELECT id FROM users WHERE db_role = current_user));

CREATE POLICY user_isolation_chunks ON chunks FOR SELECT
USING (user_id IN (SELECT id FROM users WHERE db_role = current_user));
```

**Role naming**: `db_role = "user_{uuid[:8]}"`. The full UUID is stored in `user_id` columns; RLS joins back to `users` to match the role name to the UUID.

**Superuser bypass**: `FORCE ROW LEVEL SECURITY` is set on both tables, but the `docqa` role is exempted (`NO FORCE ROW LEVEL SECURITY`). This lets the backend and worker perform administrative writes without hitting the user-scoped policies.
