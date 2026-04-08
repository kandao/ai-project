"""SQLite database tool with auto-created sample data."""

import sqlite3
from pathlib import Path

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


def query_database(sql: str) -> str:
    """Execute a SQL query and return results."""
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
            lines = [" | ".join(cols)]
            lines.append("-" * len(lines[0]))
            for row in rows[:100]:
                lines.append(" | ".join(str(row[c]) for c in cols))
            result = "\n".join(lines)
            if len(rows) > 100:
                result += f"\n... ({len(rows)} total rows, showing first 100)"
            return result
        else:
            conn.commit()
            return f"OK. Rows affected: {cursor.rowcount}"
    except Exception as e:
        return f"SQL Error: {e}"
    finally:
        conn.close()
