# -*- coding: utf-8 -*-
"""Panel utilities."""
from __future__ import annotations
import os, logging
from ftm2.db import init_db

log = logging.getLogger(__name__)


def _db_path() -> str:
    return os.getenv("DB_PATH", "./runtime/trader.db")


def _db_upsert_exec_active(is_on: bool) -> None:
    """Persist exec.active flag into config table."""
    try:
        with init_db(_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO config(key, val) VALUES('exec.active', ?)
                ON CONFLICT(key) DO UPDATE SET val=excluded.val
                """,
                ("1" if is_on else "0",),
            )
            conn.commit()
    except Exception as e:
        log.warning("E_DB_UPSERT_EXEC_ACTIVE %r", e)
