from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Optional

log = logging.getLogger("ftm2.db.core")

_DB_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: str | None = None


def get_conn(path: Optional[str] = None) -> sqlite3.Connection:
    """Return a shared SQLite connection ensuring schema availability."""

    global _CONN, _DB_PATH

    resolved = path or os.getenv("DB_PATH", "ftm2.sqlite3")
    with _DB_LOCK:
        if _CONN is None or _DB_PATH != resolved:
            if _CONN is not None:
                try:
                    _CONN.close()
                except Exception:  # pragma: no cover - best effort close
                    pass
            os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
            _CONN = sqlite3.connect(
                resolved,
                timeout=10.0,
                check_same_thread=False,
                isolation_level=None,  # autocommit
            )
            _CONN.row_factory = sqlite3.Row
            _CONN.execute("PRAGMA journal_mode=WAL;")
            _CONN.execute("PRAGMA synchronous=NORMAL;")
            _ensure_schema(_CONN)
            _DB_PATH = resolved
        return _CONN


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # config key-value storage
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config(
          key TEXT PRIMARY KEY,
          val TEXT,
          ts INTEGER
        );
        """
    )
    cols = [row[1] for row in conn.execute("PRAGMA table_info(config)")]
    if "val" not in cols:
        conn.execute("ALTER TABLE config ADD COLUMN val TEXT")
    if "ts" not in cols:
        conn.execute("ALTER TABLE config ADD COLUMN ts INTEGER")
    # migrate legacy columns if present
    if "value" in cols:
        conn.execute("UPDATE config SET val = value WHERE val IS NULL")
    if "v" in cols:
        conn.execute("UPDATE config SET val = v WHERE val IS NULL")

    # idem key registry (atomic reservations)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idem_keys(
          key TEXT PRIMARY KEY,
          symbol TEXT,
          side TEXT,
          anchor_tf TEXT,
          bar_ts INTEGER,
          created_ts INTEGER,
          expires_ts INTEGER
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_idem_exp ON idem_keys(expires_ts);"
    )


def config_set(k: str, v: str) -> None:
    conn = get_conn()
    ts = int(time.time())
    with _DB_LOCK:
        conn.execute(
            """
            INSERT INTO config(key, val, ts) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET val=excluded.val, ts=excluded.ts
            """,
            (k, v, ts),
        )


def config_get(k: str, default=None):
    conn = get_conn()
    with _DB_LOCK:
        cur = conn.execute("SELECT val FROM config WHERE key=?", (k,))
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else default


def idem_reserve(
    key: str,
    symbol: str,
    side: str,
    anchor_tf: str,
    bar_ts: int,
    ttl_ms: int,
) -> bool:
    """Try to reserve an idempotency key for the specified bar context."""

    now_ms = int(time.time() * 1000)
    expires_ts = int(bar_ts + max(ttl_ms, 0))
    conn = get_conn()

    with _DB_LOCK:
        try:
            conn.execute("DELETE FROM idem_keys WHERE expires_ts < ?", (now_ms,))
            conn.execute(
                """
                INSERT INTO idem_keys(
                  key, symbol, side, anchor_tf, bar_ts, created_ts, expires_ts
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (key, symbol, side, anchor_tf, bar_ts, now_ms, expires_ts),
            )
            return True
        except sqlite3.IntegrityError:
            log.info("DB.IDEM.RESERVE.CONFLICT key=%s", key)
            return False
        except Exception:
            log.exception("DB.IDEM.RESERVE.ERROR key=%s", key)
            return False


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Backwards compatible helper returning the shared connection."""

    return get_conn(db_path)

