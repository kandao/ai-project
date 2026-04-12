# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DocQA is a multi-service document Q&A system with an AI agent. Users upload documents (PDF/DOCX/TXT/MD), which get ingested into a vector database, and then ask questions via a chat interface powered by a ReAct agent that can retrieve documents, query databases, check stock prices, and more.

## Architecture

Three Python services communicate via Kafka and Redis:

- **backend/** — FastAPI REST API (port 8000). Handles auth (JWT), document uploads, chat SSE relay, rate limiting, and per-user DB credential management (one-time token exchange).
- **worker/** — Kafka consumer (`doc.ingest` topic). Extracts text, detects language (Japanese/English), chunks, embeds (Voyage AI or Cohere), stores in pgvector.
- **agent/** — Kafka consumer (`chat.query` topic) or standalone CLI. Runs a ReAct loop (Anthropic/OpenAI) with tools: hybrid retrieval, database queries, stock prices, CSV analysis, PDF/DOCX extraction. Has a security layer (policy engine, prompt injection scanner, audit logging).

Infrastructure: PostgreSQL+pgvector (with RLS for per-user data isolation), Kafka (via Zookeeper), Redis (SSE relay + rate limiting).

### Key data flow
1. Chat: Client → Backend (publishes to Kafka `chat.query` with one-time token) → Agent (exchanges token for scoped DB creds, runs ReAct loop, streams response via Redis pub/sub) → Backend (relays SSE to client)
2. Ingestion: Client → Backend (publishes to Kafka `doc.ingest`) → Worker (extract → chunk → embed → store in pgvector)

### Security model
- Agent receives a one-time token per chat request, exchanges it with backend's internal API for scoped PostgreSQL credentials
- DB credentials never reach the LLM — injected via Python closures in tool handlers
- PostgreSQL Row-Level Security enforces per-user data isolation
- Policy engine (`agent/security/`) controls tool allowlists per mode (CLI vs Kafka), rate limits, argument validation, path sandboxing
- Prompt injection scanner runs on user messages before the ReAct loop

## Build & Run Commands

### Full stack (Docker Compose)
```bash
docker compose up -d                    # start all services
docker compose logs -f backend          # follow backend logs
docker compose down                     # stop all
```

### Testing
Tests are E2E and require running infrastructure:
```bash
# Start test infrastructure
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d

# Run all tests
pip install -r requirements-test.txt
pytest

# Run a single test file
pytest tests/e2e/test_auth.py

# Run tests matching a keyword
pytest -k "rate_limit"

# Run only e2e-marked tests
pytest -m e2e
```

Test config uses `docqa_test` database, Redis DB 1, mock embedding/LLM providers. The `conftest.py` auto-truncates all tables and flushes Redis between tests.

### Agent CLI mode (standalone, no Docker)
```bash
cd agent
cp .env.example .env   # fill in API keys
python agent.py         # interactive REPL
```

### Per-service dependencies
Each service has its own `requirements.txt`:
- `backend/requirements.txt`
- `worker/requirements.txt`
- `agent/requirements.txt`
- `requirements-test.txt` (test runner)

## Configuration

All services use `pydantic-settings` with env vars. See `.env.example` files in `backend/`, `worker/`, `agent/`. Key env vars:

- `LLM_PROVIDER`: `anthropic` (default) or `openai` — switches the agent's LLM
- `EMBEDDING_PROVIDER`: `voyage` or `cohere` (worker) or `mock` (tests)
- `DATABASE_URL`: async (`postgresql+asyncpg://`) for backend, sync (`postgresql://`) for worker/agent
- `KAFKA_BOOTSTRAP_SERVERS`: presence of this var switches the agent from CLI to Kafka consumer mode

## Database

Schema is in `init.sql`. Uses pgvector extension for vector similarity search and pg_trgm for trigram/BM25-style text search. IVFFlat index on chunk embeddings (1024 dimensions). RLS policies on `chunks` and `documents` tables.
