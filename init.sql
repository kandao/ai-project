-- DocQA PostgreSQL initialization script
-- Executed automatically when the postgres container first starts.

-- ── Extensions ───────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for BM25-style text search

-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email            VARCHAR(255) UNIQUE NOT NULL,
    name             VARCHAR(255),
    hashed_password  VARCHAR(255) NOT NULL DEFAULT '',
    db_role          VARCHAR(64),              -- PostgreSQL role name assigned on registration
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- ── One-time tokens ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token       VARCHAR(64) UNIQUE NOT NULL,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ
);

-- Partial index: fast lookup of unconsumed tokens only
CREATE UNIQUE INDEX IF NOT EXISTS idx_tokens_token_unconsumed
    ON tokens (token)
    WHERE consumed_at IS NULL;

-- ── Documents ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    VARCHAR(255) NOT NULL,
    file_path   TEXT NOT NULL,
    file_type   VARCHAR(10) NOT NULL,         -- pdf, docx, txt, md
    file_size   INTEGER NOT NULL,             -- bytes
    status      VARCHAR(20) NOT NULL DEFAULT 'processing',  -- processing, ready, failed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents (user_id);

-- ── Chat sessions ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(255),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id);

-- ── Chat messages ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        VARCHAR(10) NOT NULL,         -- user, assistant
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',           -- token_count, latency_ms, model, provider
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id);

-- ── Chunks (populated by ingestion worker) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    id          SERIAL PRIMARY KEY,
    doc_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',
    embedding   vector(1536),
    language    VARCHAR(5) DEFAULT 'en',
    chunk_index INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_id ON chunks (user_id);

-- IVFFlat index for approximate nearest-neighbour cosine similarity search.
-- Lists = 100 is a good default for up to ~1 M rows; tune as needed.
-- Embedding dimension: 1536 (OpenAI text-embedding-3-small).
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_cosine
    ON chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- GIN index for full-text / trigram search
CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm
    ON chunks
    USING gin (content gin_trgm_ops);

-- ── Row-level Security ───────────────────────────────────────────────────────
-- Enable RLS on user-scoped tables so per-user PostgreSQL roles can only
-- read their own rows.

ALTER TABLE chunks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

-- Policy: a user's DB role (e.g. "user_abc12345") may only SELECT rows
-- where user_id matches current_user (the connected PostgreSQL role name).

-- For documents: current_user == role name == "user_{user_id[:8]}"
-- We store the full UUID in user_id, so we match on the role prefix.
-- DROP ... IF EXISTS makes this script idempotent on re-runs.
DROP POLICY IF EXISTS user_isolation_documents ON documents;
CREATE POLICY user_isolation_documents ON documents
    FOR SELECT
    USING (
        user_id IN (
            SELECT id FROM users WHERE db_role = current_user
        )
    );

-- For chunks: same logic via user_id column
DROP POLICY IF EXISTS user_isolation_chunks ON chunks;
CREATE POLICY user_isolation_chunks ON chunks
    FOR SELECT
    USING (
        user_id IN (
            SELECT id FROM users WHERE db_role = current_user
        )
    );

-- Allow the application superuser (docqa) to bypass RLS
ALTER TABLE chunks    FORCE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

-- The docqa role bypasses RLS for administrative access
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'docqa') THEN
        ALTER TABLE chunks    NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE documents NO FORCE ROW LEVEL SECURITY;
    END IF;
END $$;
