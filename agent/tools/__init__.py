"""
tools — Tool registry for Agent01.

Exposes:
    TOOLS          list[dict]   — Anthropic tool schemas for the LLM
    TOOL_HANDLERS  dict         — name → callable(**kwargs) → str
"""

from tools.stocks import get_stock_price
from tools.database import query_database
from tools.pdf_extractor import extract_pdf
from tools.doc_extractor import extract_doc
from tools.retrieval import hybrid_retrieval

# ── Handler functions (all return str) ────────────────────────────────

def _analyze_csv(file_path: str, query: str = "") -> str:
    """Load a CSV and compute basic stats or answer a query."""
    try:
        import pandas as pd
        df = pd.read_csv(file_path)
        info = f"Shape: {df.shape}\nColumns: {list(df.columns)}\n"
        info += f"Dtypes:\n{df.dtypes}\n\n"
        info += f"Head:\n{df.head(10).to_string()}\n\n"
        info += f"Describe:\n{df.describe().to_string()}"
        if query:
            try:
                # Use df.eval() — operates on column expressions only, no arbitrary Python
                result = df.eval(query)
                info += f"\n\nQuery result:\n{result}"
            except Exception as e:
                info += f"\n\nQuery error: {e}"
        return info[:50000]
    except Exception as e:
        return f"Error: {e}"


def _generate_chart(data: str, chart_type: str = "bar",
                    title: str = "Chart", output_path: str = "chart.png") -> str:
    """Generate a chart from JSON data."""
    try:
        import json
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        parsed = json.loads(data)
        labels = list(parsed.keys())
        values = list(parsed.values())

        plt.figure(figsize=(10, 6))
        if chart_type == "bar":
            plt.bar(labels, values)
        elif chart_type == "line":
            plt.plot(labels, values, marker="o")
        elif chart_type == "pie":
            plt.pie(values, labels=labels, autopct="%1.1f%%")
        else:
            plt.bar(labels, values)
        plt.title(title)
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        return f"Chart saved to {output_path}"
    except Exception as e:
        return f"Error: {e}"


# ── Registry ──────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "get_stock_price":   lambda **kw: get_stock_price(kw["symbol"]),
    "query_database":    lambda **kw: query_database(kw["sql"]),
    "hybrid_retrieval":  lambda **kw: hybrid_retrieval(kw["query"], kw.get("top_k", 5)),
    "analyze_csv":     lambda **kw: _analyze_csv(kw["file_path"], kw.get("query", "")),
    "generate_chart":  lambda **kw: _generate_chart(
        kw["data"], kw.get("chart_type", "bar"),
        kw.get("title", "Chart"), kw.get("output_path", "chart.png")),
    "extract_pdf":     lambda **kw: extract_pdf(kw["file_path"]),
    "extract_doc":     lambda **kw: extract_doc(kw["file_path"]),
}

TOOLS = [
    {
        "name": "get_stock_price",
        "description": "Get real-time stock price and basic info for a ticker symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL"}
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "query_database",
        "description": "Execute a SQL query against the local SQLite database (sample products and employees).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL query to execute"}
            },
            "required": ["sql"],
        },
    },
    {
        "name": "analyze_csv",
        "description": "Load a CSV file, show shape/columns/stats. Optionally run a pandas query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to CSV file"},
                "query": {"type": "string", "description": "Optional pandas expression to evaluate"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "generate_chart",
        "description": "Generate a chart (bar, line, or pie) from JSON data and save as image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": 'JSON object like {"label": value, ...}'},
                "chart_type": {"type": "string", "enum": ["bar", "line", "pie"]},
                "title": {"type": "string"},
                "output_path": {"type": "string"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "extract_pdf",
        "description": "Extract text content from a PDF file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to PDF file"}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "extract_doc",
        "description": "Extract text content from a DOCX file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to DOCX file"}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "hybrid_retrieval",
        "description": (
            "Search the document knowledge base using hybrid vector + keyword (BM25) retrieval "
            "with RRF fusion. Use this to answer questions grounded in uploaded documents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                },
            },
            "required": ["query"],
        },
    },
]
