"""
database.py — SQL query tool.

Supports two backends:
  - PostgreSQL: when db_url is provided (or DATABASE_URL env var is set), connects via psycopg2
  - SQLite:     fallback using a local sample database

Agent04 changes:
  - Only SELECT statements allowed
  - Write operations blocked via WRITE_PATTERNS
  - Dangerous patterns blocked via DANGEROUS_PATTERNS
  - Query length capped at 2000 chars
"""

import re
import sqlite3
from pathlib import Path
from typing import Optional

# SQL statements that are NEVER allowed via the LLM tool
WRITE_PATTERNS = re.compile(
    r"(?i)\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|REPLACE)\b"
)

DANGEROUS_PATTERNS = re.compile(
    r"(?i)("
    r"INTO\s+OUTFILE|"
    r"LOAD_FILE|"
    r"pg_read_file|"
    r"pg_ls_dir|"
    r"COPY\s+.*\s+TO|"
    r";\s*--|"            # comment-based injection
    r"UNION\s+SELECT|"   # UNION injection
    r"information_schema|"
    r"pg_catalog\.pg_shadow"
    r")"
)

DB_PATH = Path(__file__).parent.parent / "data" / "sample.db"


def _ensure_db():
    """Create sample database if it doesn't exist."""
    if DB_PATH.exists():
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            price REAL,
            stock INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT,
            salary REAL,
            hire_date TEXT
        )
    """)
    products = [
        ("Laptop Pro", "Electronics", 1299.99, 45),
        ("Wireless Mouse", "Electronics", 29.99, 200),
        ("Standing Desk", "Furniture", 599.00, 30),
        ("Monitor 27\"", "Electronics", 449.99, 60),
        ("Ergonomic Chair", "Furniture", 399.00, 25),
        ("Keyboard Mechanical", "Electronics", 89.99, 150),
        ("Webcam HD", "Electronics", 79.99, 100),
        ("Desk Lamp", "Furniture", 49.99, 80),
    ]
    c.executemany("INSERT INTO products (name,category,price,stock) VALUES (?,?,?,?)", products)
    employees = [
        ("Alice Chen", "Engineering", 120000, "2022-01-15"),
        ("Bob Smith", "Marketing", 85000, "2021-06-01"),
        ("Carol Wu", "Engineering", 135000, "2020-03-10"),
        ("David Lee", "Sales", 90000, "2023-02-20"),
        ("Eve Park", "Engineering", 110000, "2022-09-01"),
    ]
    c.executemany("INSERT INTO employees (name,department,salary,hire_date) VALUES (?,?,?,?)", employees)
    conn.commit()
    conn.close()


def _format_rows(cols, rows, total: int) -> str:
    """Format query rows into a pipe-delimited table string."""
    lines = [" | ".join(str(c) for c in cols)]
    lines.append("-" * len(lines[0]))
    for row in rows[:100]:
        lines.append(" | ".join(str(v) for v in row))
    result = "\n".join(lines)
    if total > 100:
        result += f"\n... ({total} total rows, showing first 100)"
    return result


def _query_postgres(sql: str, db_url: str) -> str:
    """Execute SQL against a PostgreSQL database via psycopg2."""
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute(sql)
            if sql.strip().upper().startswith("SELECT"):
                rows = cur.fetchall()
                if not rows:
                    return "No results."
                cols = [desc[0] for desc in cur.description]
                return _format_rows(cols, rows, len(rows))
            else:
                conn.commit()
                return f"OK. Rows affected: {cur.rowcount}"
    except Exception as e:
        return f"SQL Error: {e}"
    finally:
        conn.close()


def _query_sqlite(sql: str) -> str:
    """Execute SQL against the local SQLite sample database."""
    _ensure_db()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql)
        if sql.strip().upper().startswith("SELECT"):
            rows = cursor.fetchall()
            if not rows:
                return "No results."
            cols = rows[0].keys()
            return _format_rows(cols, [(row[c] for c in cols) for row in rows], len(rows))
        else:
            conn.commit()
            return f"OK. Rows affected: {cursor.rowcount}"
    except Exception as e:
        return f"SQL Error: {e}"
    finally:
        conn.close()


def query_database(sql: str, db_url: Optional[str] = None) -> str:
    """
    Execute a READ-ONLY SQL query with validation.

    Args:
        sql:    SQL query to execute.
        db_url: Optional PostgreSQL connection URL. If provided (or DATABASE_URL is set),
                connects to PostgreSQL. Otherwise uses the local SQLite sample database.

    Agent04 changes:
      - Only SELECT statements allowed
      - Write operations blocked
      - Dangerous patterns blocked
      - Query length capped at 2000 chars
    """
    import os

    sql = sql.strip()

    # Length limit
    if len(sql) > 2000:
        return "Error: Query too long (max 2000 chars)"

    # Must start with SELECT
    if not sql.upper().startswith("SELECT"):
        return "Error: Only SELECT queries are allowed"

    # Block write operations
    if WRITE_PATTERNS.search(sql):
        return "Error: Write operations are not permitted"

    # Block dangerous patterns
    if DANGEROUS_PATTERNS.search(sql):
        return "Error: Query contains disallowed patterns"

    resolved_url = db_url or os.getenv("DATABASE_URL")
    if resolved_url:
        return _query_postgres(sql, resolved_url)
    return _query_sqlite(sql)
