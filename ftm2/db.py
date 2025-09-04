# -*- coding: utf-8 -*-
"""SQLite helpers and schema migration."""
from __future__ import annotations

import sqlite3

# [ANCHOR:DB_INIT]

def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def init_db(db_path: str = "./runtime/trader.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY
            -- val column is added below if missing
        )
        """
    )
    if not _col_exists(conn, "config", "val"):
        conn.execute("ALTER TABLE config ADD COLUMN val TEXT")
        try:
            conn.execute("UPDATE config SET val = value WHERE val IS NULL")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    return conn

__all__ = ["init_db"]
