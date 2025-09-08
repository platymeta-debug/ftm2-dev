import os
import os
import sqlite3

_conn: sqlite3.Connection | None = None


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = db_path or os.getenv("DB_PATH", "./runtime/trader.db")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA synchronous=NORMAL;")
    return _conn


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())


def init_db(db_path: str = "./runtime/trader.db") -> sqlite3.Connection:
    conn = get_conn(db_path)
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
