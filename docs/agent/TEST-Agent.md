# Agent — E2E Test Plan

> Test plan for the ReAct Agent: loop mechanics, tool dispatch, context management, Kafka consumer integration, and per-user access control.

---

## Scope

This plan covers the agent system across all three PRD iterations:

| Layer | Component | Source |
|-------|-----------|--------|
| **Agent01** | ReAct loop, base tools, domain tools, TodoManager, SkillLoader, context compaction, BackgroundManager | `loop.py`, `tools/` |
| **Agent02** | Kafka consumer, Redis streaming, hybrid retrieval | `consumer.py`, `tools/retrieval.py` |
| **Agent03** | Token exchange, scoped DB credentials, per-user isolation | `auth.py`, `consumer.py` |

---

## 1. ReAct Loop (`agent/loop.py`)

### Unit Tests — `tests/unit/agent/test_loop.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 1.1 | Single-turn (no tools) | LLM returns text with `stop_reason="end_turn"` | Loop exits after 1 call, assistant message appended |
| 1.2 | Tool call → result → final answer | LLM calls a tool, then answers | 2 LLM calls: tool_use round + final text |
| 1.3 | Multi-tool round | LLM calls 2 tools in one response | Both dispatched, both results fed back |
| 1.4 | Multi-step reasoning | LLM calls tool A, then tool B, then answers | 3 LLM calls, correct message history |
| 1.5 | Unknown tool | LLM calls non-existent tool name | Result: `"Unknown tool: foo"`, no crash |
| 1.6 | Tool error handling | Tool handler raises exception | Result: `"Error: ..."`, loop continues |
| 1.7 | Custom tools/handlers injection | Pass custom `tools` and `handlers` args | Only custom tools used (not globals) |
| 1.8 | Messages mutated in-place | Check `messages` list after loop | Contains all assistant + tool_result messages |

### Mock strategy:
Mock `llm.llm_client.chat()` to return controlled responses with/without tool_use blocks.

---

## 2. Base Tools (`agent/loop.py`)

### Unit Tests — `tests/unit/agent/test_base_tools.py`

#### 2.1 Bash Tool

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 2.1.1 | Simple command | `echo hello` | `"hello"` |
| 2.1.2 | Command with stderr | `ls /nonexistent` | Error output captured |
| 2.1.3 | Dangerous command blocked | `rm -rf /` | `"Error: Dangerous command blocked"` |
| 2.1.4 | Sudo blocked | `sudo apt install foo` | `"Error: Dangerous command blocked"` |
| 2.1.5 | Timeout | `sleep 200` | `"Error: Timeout (120s)"` |
| 2.1.6 | Output truncation | Command producing >50KB output | Truncated to 50,000 chars |

#### 2.2 File I/O Tools

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 2.2.1 | read_file | Existing file path | File contents returned |
| 2.2.2 | read_file with limit | `limit=5` on 100-line file | First 5 lines + "... (95 more)" |
| 2.2.3 | read_file — not found | Non-existent path | `"Error: ..."` |
| 2.2.4 | write_file | New file path + content | File created with content |
| 2.2.5 | write_file — nested dirs | `subdir/nested/file.txt` | Directories created automatically |
| 2.2.6 | edit_file | Existing file, old_text, new_text | Text replaced exactly once |
| 2.2.7 | edit_file — text not found | old_text not in file | `"Error: Text not found in ..."` |
| 2.2.8 | Path escape blocked | `../../etc/passwd` | `ValueError("Path escapes workspace")` |

---

## 3. Domain Tools (`agent/tools/`)

### Unit Tests — `tests/unit/agent/test_domain_tools.py`

#### 3.1 Stock Price (`tools/stocks.py`)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.1.1 | Valid ticker | `AAPL` | Returns price info string (mock yfinance) |
| 3.1.2 | Invalid ticker | `ZZZZZZZ` | Error message, no crash |

#### 3.2 Database Query (`tools/database.py`)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.2.1 | SQLite SELECT | `SELECT * FROM products` | Pipe-delimited table with 8 products |
| 3.2.2 | SQLite — no results | `SELECT * FROM products WHERE price > 99999` | `"No results."` |
| 3.2.3 | SQLite — invalid SQL | `SELCET * FORM products` | `"SQL Error: ..."` |
| 3.2.4 | PostgreSQL mode | `SELECT 1` with valid `db_url` | `"1"` result |
| 3.2.5 | Result truncation | Query returning >100 rows | First 100 rows + `"... (N total rows)"` |
| 3.2.6 | Auto-create sample DB | Delete `data/sample.db`, call query | DB recreated with sample data |

#### 3.3 CSV Analysis (`tools/__init__.py`)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.3.1 | Basic CSV analysis | Valid CSV path | Shape, columns, dtypes, head, describe |
| 3.3.2 | CSV with query | `query="df['price'].mean()"` | Query result appended |
| 3.3.3 | CSV — file not found | Non-existent path | `"Error: ..."` |
| 3.3.4 | CSV — invalid query | `query="invalid_expression"` | `"Query error: ..."` |

#### 3.4 Chart Generation (`tools/__init__.py`)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.4.1 | Bar chart | Valid JSON data + `chart_type="bar"` | `"Chart saved to chart.png"`, file exists |
| 3.4.2 | Line chart | `chart_type="line"` | File saved |
| 3.4.3 | Pie chart | `chart_type="pie"` | File saved |
| 3.4.4 | Invalid JSON | Malformed data string | `"Error: ..."` |

#### 3.5 PDF/DOCX Extraction (`tools/pdf_extractor.py`, `tools/doc_extractor.py`)

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 3.5.1 | Extract valid PDF | PDF with text | Text content returned |
| 3.5.2 | Extract valid DOCX | DOCX with paragraphs | Paragraph text returned |
| 3.5.3 | Extract — invalid file | Random bytes | `"Error: ..."` |

---

## 4. TodoManager (`agent/loop.py`)

### Unit Tests — `tests/unit/agent/test_todo.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 4.1 | Create todos | 3 items, all `pending` | `render()` shows 3 `[ ]` items |
| 4.2 | Mark in_progress | 1 item with `in_progress` | Shows `[>]` with activeForm suffix |
| 4.3 | Mark completed | 1 item `completed` | Shows `[x]`, counter `(1/3 completed)` |
| 4.4 | Max 20 items | 21 items | `ValueError("Max 20 todos")` |
| 4.5 | Only one in_progress | 2 items with `in_progress` | `ValueError("Only one in_progress")` |
| 4.6 | Missing content | Item without `content` | `ValueError("content required")` |
| 4.7 | Missing activeForm | Item without `activeForm` | `ValueError("activeForm required")` |
| 4.8 | Invalid status | `status="invalid"` | `ValueError("invalid status")` |
| 4.9 | has_open_items | Mix of completed/pending | `True` if any non-completed |
| 4.10 | has_open_items — all done | All `completed` | `False` |
| 4.11 | Nag reminder trigger | 3 rounds without TodoWrite, open items exist | Reminder injected into results |

---

## 5. SkillLoader (`agent/loop.py`)

### Unit Tests — `tests/unit/agent/test_skills.py`

| # | Test Case | Input | Expected |
|---|-----------|-------|----------|
| 5.1 | Load skill from directory | `skills/analyst/SKILL.md` exists | Skill parsed with name, description, body |
| 5.2 | Descriptions in system prompt | Multiple skills | `descriptions()` lists all skill names + descriptions |
| 5.3 | Load unknown skill | `load("nonexistent")` | `"Error: Unknown skill..."` with available list |
| 5.4 | YAML frontmatter parsing | Skill with `name:` and `description:` | Correctly parsed metadata |
| 5.5 | Empty skills directory | No `SKILL.md` files | `descriptions()` returns `"(no skills loaded)"` |
| 5.6 | Skill body injection | `load("analyst")` | Returns `<skill name="analyst">...</skill>` |

---

## 6. Context Compaction (`agent/loop.py`)

### Unit Tests — `tests/unit/agent/test_context.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 6.1 | estimate_tokens | JSON-serialized messages | ~len(json)/4 |
| 6.2 | microcompact — ≤3 results | 3 tool_result blocks | No clearing |
| 6.3 | microcompact — >3 results | 6 tool_result blocks | First 3 cleared to `[cleared]` |
| 6.4 | microcompact — short results preserved | Tool result <100 chars | Not cleared (even if old) |
| 6.5 | auto_compact — transcript saved | Trigger auto_compact | `.transcripts/transcript_*.jsonl` created |
| 6.6 | auto_compact — summary returned | Trigger auto_compact | Returns list with 1 message containing summary |
| 6.7 | auto_compact — LLM summarize called | Trigger auto_compact | `chat()` called with summarization prompt |
| 6.8 | Token threshold trigger | Messages > 150K estimated tokens | `auto_compact` triggered in loop |

---

## 7. BackgroundManager (`agent/loop.py`)

### Unit Tests — `tests/unit/agent/test_background.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 7.1 | Run background command | `background_run("echo hello")` | Returns `"Background task <id> started: ..."` |
| 7.2 | Check task status | Run + wait + check | Status: `completed`, result: `"hello"` |
| 7.3 | Check unknown task | `check("nonexistent")` | `"Unknown: nonexistent"` |
| 7.4 | Drain notifications | Run task, wait, drain | Notification with task_id, status, result |
| 7.5 | Drain empty queue | No tasks run | Empty list |
| 7.6 | Task timeout | `background_run("sleep 200", timeout=1)` | Status: `error`, timeout message |
| 7.7 | Multiple concurrent tasks | Run 3 tasks | All complete independently |
| 7.8 | Notifications injected in loop | Background task completes during loop | `<background-results>` message added |

---

## 8. LLM Client (`agent/llm/llm_client.py`)

### Unit Tests — `tests/unit/agent/test_llm_client.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 8.1 | Anthropic provider | `LLM_PROVIDER=anthropic` | Uses `anthropic.Anthropic().messages.create()` |
| 8.2 | OpenAI provider | `LLM_PROVIDER=openai` | Uses `openai.OpenAI().chat.completions.create()` |
| 8.3 | chat() with tools | Pass tool schemas | Tools forwarded to provider |
| 8.4 | stream() yields chunks | Stream response | Generator yields text strings |
| 8.5 | Base URL override | `ANTHROPIC_BASE_URL=https://custom.api/` | Client created with custom base_url |
| 8.6 | Default model selection | No `LLM_MODEL` set | Provider-specific default used |
| 8.7 | Model override | `LLM_MODEL=custom-model` | Override model used |

---

## 9. Kafka Consumer (`agent/consumer.py`)

### Integration Tests — `tests/integration/agent/test_consumer.py`

Requires: Kafka, Redis, PostgreSQL, Backend (for token exchange)

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 9.1 | Process valid message | Publish `chat.query` with valid token | `process_message()` completes, Redis receives `chunk:` + `[DONE]` |
| 9.2 | Missing token | Publish message without `token` field | Redis receives `error:Missing authentication token` + `[DONE]` |
| 9.3 | Invalid token | Publish message with fake token | Redis receives `error:Authentication failed` + `[DONE]` |
| 9.4 | Redis streaming format | Process message, subscribe to Redis | Messages follow `chunk:<text>` protocol |
| 9.5 | Final text extraction | Mock agent_loop, check `_extract_final_text` | Correctly extracts text from Anthropic content blocks |
| 9.6 | Extract from plain string content | `{"role": "assistant", "content": "Hello"}` | Returns `"Hello"` |
| 9.7 | Extract from content block list | `[{"type": "text", "text": "Hello"}]` | Returns `"Hello"` |
| 9.8 | Chunk streaming granularity | Process message with 200-char response | Multiple `chunk:` messages (~50 chars each) |
| 9.9 | Agent error → Redis error | `agent_loop` raises exception | Redis receives `error:Agent error: ...` + `[DONE]` |
| 9.10 | Consumer loop shutdown | Send SIGTERM | Consumer closes gracefully |

---

## 10. Per-User Access Control (`agent/auth.py`)

### Unit Tests — `tests/unit/agent/test_auth.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 10.1 | exchange_token — success | Valid token, backend returns 200 | Returns credentials dict |
| 10.2 | exchange_token — invalid | Backend returns 401 | Raises `AuthError` |
| 10.3 | exchange_token — timeout | Backend unreachable | Raises (requests.Timeout or AuthError) |
| 10.4 | build_db_url | Credentials dict | `"postgresql://user:pass@host:port/dbname"` |
| 10.5 | build_db_url — default port | No `db_port` in creds | Uses port `5432` |

### Integration Tests — `tests/integration/agent/test_scoped_tools.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 10.6 | Scoped tools built correctly | `build_scoped_tools(db_url)` | `hybrid_retrieval` and `query_database` use provided db_url |
| 10.7 | LLM never sees db_url | Inspect tool schemas from `build_scoped_tools` | No `db_url` parameter in any schema |
| 10.8 | Scoped retrieval | Scoped handler called with query | Uses scoped db_url, not global DATABASE_URL |
| 10.9 | Scoped query_database | Scoped handler called with SQL | Uses scoped db_url for PostgreSQL |
| 10.10 | Base tools unchanged | Check handlers for `bash`, `read_file`, etc. | Same as global ALL_HANDLERS |

---

## 11. Agent02/03 E2E Flow

### E2E Tests — `tests/e2e/test_agent_e2e.py`

Requires: Full stack

| # | Test Case | Steps | Expected |
|---|-----------|-------|----------|
| 11.1 | Chat → tool selection → response | Send chat, verify agent chose appropriate tool | SSE response with relevant content |
| 11.2 | Multi-tool reasoning | Ask question requiring 2+ tools | Agent calls multiple tools in sequence |
| 11.3 | Token exchange → scoped query | Chat triggers retrieval | Token exchanged, scoped DB used, results returned |
| 11.4 | Agent error recovery | Cause tool failure, verify agent retries/adapts | Agent recovers, provides response |
| 11.5 | Streaming latency | Measure time-to-first-chunk | First `chunk:` message within expected window |
| 11.6 | Context compaction in long session | Send 20+ messages to same session | Agent compacts context, continues functioning |
| 11.7 | Concurrent queries | 3 users chat simultaneously | All receive correct responses (no cross-contamination) |
| 11.8 | Credential isolation | User A and B chat concurrently | Each agent uses respective scoped credentials |

---

## 12. Security Tests

### Tests — `tests/security/test_agent_security.py`

| # | Test Case | Description | Expected |
|---|-----------|-------------|----------|
| 12.1 | DB credentials not in messages | Inspect full message history after loop | No `db_url`, `db_password`, or connection strings in any message |
| 12.2 | DB credentials not in transcript | Trigger auto_compact, read transcript file | No credentials in transcript JSONL |
| 12.3 | Dangerous bash blocked | Agent tries `rm -rf /` via bash tool | Blocked, error returned |
| 12.4 | Path traversal blocked | Agent tries to read `../../etc/passwd` | `ValueError` raised, file not read |
| 12.5 | Token single-use enforcement | Exchange same token twice in agent flow | Second exchange returns 401 |
| 12.6 | SQL injection via query_database | User message contains SQL injection prompt | Agent's query_database uses parameterized connection, no data leak |
| 12.7 | Tool result size limit | Tool returns >50KB | Truncated to 50,000 chars |

---

## Test Infrastructure Notes

### Mocking the LLM

For unit tests of the ReAct loop, mock `llm.llm_client.chat()`:

```python
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

def make_text_response(text):
    """Simulate LLM response with no tool calls (end_turn)."""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason="end_turn")

def make_tool_response(tool_name, tool_input, tool_id="tool_1"):
    """Simulate LLM response requesting a tool call."""
    block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input, id=tool_id)
    return SimpleNamespace(content=[block], stop_reason="tool_use")

@patch("loop.chat")
def test_single_turn(mock_chat):
    mock_chat.return_value = make_text_response("Hello!")
    messages = [{"role": "user", "content": "Hi"}]
    agent_loop(messages)
    assert messages[-1]["role"] == "assistant"
```

### Mocking External APIs

| API | Mock approach |
|-----|---------------|
| LLM (Anthropic/OpenAI) | `unittest.mock.patch("llm.llm_client.chat")` |
| yfinance | `unittest.mock.patch("tools.stocks.yf.Ticker")` |
| Embedding (Voyage/Cohere) | `tests/mock_embedding.py` |
| Redis | Real Redis in integration, `fakeredis` in unit |
| Kafka | Real Kafka in integration, mock producer/consumer in unit |

### Test File Organization

```
tests/
  unit/
    agent/
      test_loop.py
      test_base_tools.py
      test_domain_tools.py
      test_todo.py
      test_skills.py
      test_context.py
      test_background.py
      test_llm_client.py
      test_auth.py
    worker/
      test_extractors.py
      test_language_detection.py
      test_chunking_english.py
      test_chunking_japanese.py
      test_embedding.py
      test_chunk_quality.py
  integration/
    agent/
      test_consumer.py
      test_retrieval.py
      test_scoped_tools.py
    worker/
      test_storage.py
      test_pipeline.py
      test_kafka_consumer.py
  security/
    test_agent_security.py
  e2e/
    test_rag_flow.py
    test_agent_e2e.py
    (existing E2E tests...)
```

---

## Priority Matrix

| Priority | Tests | Rationale |
|----------|-------|-----------|
| **P0 — Must have** | 1.1-1.6, 9.1-9.4, 10.1-10.5, 12.1-12.5 | Loop correctness, consumer integration, security |
| **P1 — Should have** | 2.1-2.2, 3.2, 4.1-4.5, 6.2-6.3, 8.1-8.2, 10.6-10.10, 11.1-11.3 | Tool reliability, scoped access, E2E flows |
| **P2 — Nice to have** | 3.1, 3.3-3.5, 5.1-5.6, 7.1-7.8, 6.4-6.8, 11.4-11.8 | Complete coverage, edge cases |
