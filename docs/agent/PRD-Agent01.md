# Agent PRD — Agent01 (AI Business Analyst)

> Self-development agent. Limited scope by design — for production use, adopt the latest Agent SDK with advanced features.

---

## Purpose

A ReAct (Reasoning + Acting) loop agent that runs in two modes:

| Mode | Trigger | Role |
|---|---|---|
| **CLI** | `python agent.py` | Standalone analyst — CSV, stocks, SQL queries |
| **Kafka consumer** | `chat.query` topic | DocQA intelligence layer — retrieval validation + summarization |

Provider-agnostic via `llm/llm_client.py`. Flip `LLM_PROVIDER` to switch between Anthropic and OpenAI — no code changes.

---

## File Structure

```
agent/
  ├── agent.py                  # entrypoint — detects mode (CLI vs Kafka) and starts the loop
  ├── loop.py                   # core ReAct loop (tool-use, todo-write, skill-loading, context-compact, background)
  ├── consumer.py               # Kafka consumer — listens on chat.query, calls loop, publishes to Redis
  ├── requirements.txt
  ├── .env.example
  ├── data/
  │   └── sample.db             # auto-created on first run (SQLite, CLI mode only)
  ├── llm/
  │   ├── __init__.py
  │   └── llm_client.py         # provider abstraction — Anthropic + OpenAI
  ├── skills/                   # dynamically loaded skill definitions
  │   └── analyst/
  │       └── SKILL.md          # business analysis persona (sample skill)
  └── tools/
      ├── __init__.py           # registry: TOOLS list + TOOL_HANDLERS dict + analyze_csv + generate_chart
      ├── stocks.py             # yfinance (free, no API key)
      ├── database.py           # SQLite with sample products + employees (auto-created)
      ├── retrieval.py          # hybrid retrieval (pgvector + BM25 + RRF) — Kafka mode only
      ├── pdf_extractor.py      # PDF extraction (pdfplumber)
      └── doc_extractor.py      # DOCX extraction (python-docx)
```

---

## Environment Variables

```bash
LLM_PROVIDER=anthropic              # or "openai"
LLM_MODEL=claude-sonnet-4-6         # optional model override
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Optional: Anthropic-compatible API (e.g. MiniMax)
# ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic

# Kafka mode only
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql://...       # for pgvector retrieval
```

---

## ReAct Loop

```
Query (from CLI input or Kafka message)
  → Reason: LLM decides which tool to call (or stop)
  → Act:    execute tool, capture result
  → Observe: feed result back into context
  → repeat until stop condition
  → stream final answer → stdout (CLI) or Redis pub/sub (Kafka)
```

Error recovery: if a tool call fails, the agent receives the error as an observation and can retry with corrected arguments or fall back to a different tool.

### Kafka Mode — Autonomous Tool Selection

When triggered via `chat.query`, the agent runs the same ReAct loop but outputs to Redis instead of stdout. The LLM decides which tools to call — document retrieval is not assumed.

```
query from Kafka
  → LLM reasons: does this need documents? data analysis? live data?
  → calls the appropriate tool(s)
  → streams response → Redis: session:{id}
```

---

## Loop Mechanisms

Five mechanisms are wired into `loop.py`, extracted from the reference harness (`s_full.py`):

| # | Mechanism | Source | What it does |
|---|---|---|---|
| 1 | **tool_use** | s02 tool dispatch | Dispatches tool calls via `ALL_HANDLERS` dict. Base tools (bash, read/write/edit, TodoWrite, load_skill, compress, background) + domain tools (stocks, database, csv, chart, pdf, docx). |
| 2 | **todo_write** | s03 TodoManager | `TodoManager` class — max 20 items, one `in_progress` at a time. Nag reminder injected after 3 consecutive rounds without a TodoWrite call when open items exist. |
| 3 | **skill_loading** | s05 SkillLoader | Scans `skills/*/SKILL.md` at startup, parses YAML frontmatter (name, description). Descriptions injected into system prompt. `load_skill` tool injects full skill body into context on demand. |
| 4 | **context_compact** | s06 compression | Two-stage: `microcompact()` clears old tool results (keeps last 3) before each LLM call. `auto_compact()` triggers when estimated tokens exceed 100k — saves full transcript to `.transcripts/`, summarizes via LLM, replaces message history. Manual `/compact` REPL command also available. |
| 5 | **background tasks** | s08 BackgroundManager | Threaded shell execution via `background_run` tool. Notifications queued and drained into context before each LLM call. `check_background` tool for status polling. |

---

## Tools

All tools live in `tools/` and are registered in `tools/__init__.py`. Base tools are defined in `loop.py`. The LLM selects from them freely in both modes.

### Domain Tools (tools/)

| Tool | Source | Description |
|---|---|---|
| `get_stock_price` | `stocks.py` | Real-time stock price via yfinance |
| `query_database` | `database.py` | SQL queries against local SQLite (auto-created with sample products + employees) |
| `analyze_csv` | `__init__.py` | Load CSV, compute stats, run pandas expressions |
| `generate_chart` | `__init__.py` | Bar, line, pie charts from JSON data via matplotlib |
| `extract_pdf` | `pdf_extractor.py` | Read text from PDF files via pdfplumber |
| `extract_doc` | `doc_extractor.py` | Read text from DOCX files via python-docx |
| `hybrid_retrieval` | `retrieval.py` | pgvector + BM25 + RRF — Kafka mode only (not yet implemented) |

### Base Tools (loop.py)

| Tool | Description |
|---|---|
| `bash` | Run shell commands (dangerous commands blocked) |
| `read_file` | Read file with optional line limit |
| `write_file` | Write content to file (path-sandboxed to workspace) |
| `edit_file` | Replace exact text in file |
| `TodoWrite` | Update task checklist (max 20 items, one in_progress) |
| `load_skill` | Inject a skill definition into context |
| `compress` | Manually trigger context compaction |
| `background_run` | Run shell command in background thread |
| `check_background` | Check background task status by ID |

---

## Example Multi-Step Task

```
"Analyze sales_q1.csv, find the top 3 products by revenue,
 look up their current stock prices, and summarize the findings."

Step 1: analyze_csv("sales_q1.csv")          → top products by revenue
Step 2: get_stock_price("AAPL")              → $182.40
Step 3: get_stock_price("MSFT")             → $415.20
Step 4: get_stock_price("GOOGL")            → $172.80
Step 5: generate_chart(data, type="bar")    → chart.png
Step 6: [STOP] stream summary with chart
```

---

## LLM Client Interface

```python
# llm/llm_client.py
#
# Provider-agnostic. Supports Anthropic (+ compatible APIs like MiniMax) and OpenAI.
# Lazy client initialization — only the active provider's SDK is invoked.

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")   # or "openai"
MODEL    = os.getenv("LLM_MODEL") or DEFAULTS[PROVIDER]

def chat(messages, system="", tools=None, max_tokens=8000):
    """Send messages to LLM. Returns provider-native response."""

def stream(messages, system="", max_tokens=8000):
    """Streaming text generation. Yields text chunks."""
```

All agent logic calls `chat()` or `stream()` — never imports `anthropic` or `openai` directly.

OpenAI adapter converts Anthropic-style messages and tool schemas to OpenAI format internally.

---

## Skill Loading

Skills are injected dynamically into the agent's system prompt at startup. Each skill is a markdown file at `skills/<name>/SKILL.md` with YAML frontmatter:

```markdown
---
name: analyst
description: Business analysis persona — CSV trends, revenue breakdowns, comparative reports
---

(skill body — injected into context when load_skill is called)
```

Skill descriptions appear in the system prompt so the LLM knows what's available. The full body is loaded on demand via the `load_skill` tool.

---

## Context Management

- **microcompact**: before each LLM call, clear tool result content older than the last 3 results (replace with `[cleared]`)
- **auto_compact**: when estimated tokens exceed 100k, save full transcript to `.transcripts/transcript_<timestamp>.jsonl`, summarize via LLM, replace message history with summary
- **manual compact**: `/compact` REPL command or `compress` tool call
- Preserve tool call history (observations) over conversation history

---

## CLI REPL Commands

| Command | Action |
|---|---|
| `/compact` | Manually compress conversation context |
| `/tasks` | Show current TodoWrite checklist |
| `q` / `exit` | Quit |

---

## Dependencies

```
anthropic
openai
yfinance
pandas
matplotlib
plotly
pdfplumber
python-docx
python-dotenv
```

---

## Out of Scope (Agent01)

- Persistent memory across sessions
- Sub-agent spawning
- Web browsing / search tools
- Direct HTTP connections to the backend (communication is Kafka in / Redis out only)
- Team messaging / teammate coordination
- Shutdown protocol / plan approval gates

These are deferred to a future agent version.
