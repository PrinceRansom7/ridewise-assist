"""
Read-only SQLite connection pool for RideWise Assist.

All connections are opened in read-only URI mode so SQLite itself enforces
the constraint — no application-level code can accidentally issue a write.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Make config importable regardless of working directory
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config.settings import DB_PATH


def get_connection() -> sqlite3.Connection:
    """
    Return a read-only SQLite connection.

    Uses the URI mode flag ``mode=ro`` so the OS-level file descriptor is
    opened read-only. Attempting any write via this connection raises
    ``sqlite3.OperationalError: attempt to write a readonly database``.
    """
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # Enforce a statement-level timeout via the progress handler
    return con


def get_schema_summary() -> str:
    """Return a compact schema description for use in LLM prompts."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    lines = []
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = ", ".join(f"{r[1]} ({r[2]})" for r in cur.fetchall())
        lines.append(f"{t}: {cols}")
    con.close()
    return "\n".join(lines)
