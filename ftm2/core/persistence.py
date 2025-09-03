# -*- coding: utf-8 -*-
"""
SQLite Persistence (skeleton)
- WAL 모드, NORMAL sync
- 기본 테이블: trades, positions, pnl_daily, config, events, patches
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import logging
from typing import Any, Dict, Optional

log = logging.getLogger("ftm2.db")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# [ANCHOR:PERSISTENCE]
class Persistence:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path or "./runtime/trader.db"
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False 로 다중 스레드에서 안전하게 사용 (상단 RLock으로 직렬화)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup_pragmas()

    def _setup_pragmas(self) -> None:
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA temp_store=MEMORY;")
        log.info("[DB] open path=%s (WAL)", self.db_path)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
                log.info("[DB] closed")
            except Exception:
                pass

    # ---------- schema ----------
    def ensure_schema(self) -> None:
        ddl = [
            # trades: 체결/주문 기록(요약)
            """
            CREATE TABLE IF NOT EXISTS trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER NOT NULL,
              symbol TEXT NOT NULL,
              side TEXT,
              qty REAL,
              px REAL,
              type TEXT,
              fee REAL,
              order_id TEXT,
              client_order_id TEXT,
              link_id TEXT
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);",
            "CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades(symbol, ts);",

            # positions: 심볼별 현재 상태(업서트)
            """
            CREATE TABLE IF NOT EXISTS positions (
              symbol TEXT PRIMARY KEY,
              qty REAL,
              avg_px REAL,
              u_pnl REAL,
              r_pnl REAL,
              leverage REAL,
              liq_px REAL,
              updated_ts INTEGER
            );
            """,

            # pnl_daily: 일자 집계
            """
            CREATE TABLE IF NOT EXISTS pnl_daily (
              date TEXT PRIMARY KEY, -- YYYY-MM-DD
              realized REAL,
              fees REAL,
              net REAL,
              max_dd REAL
            );
            """,

            # config: 런타임 설정(패널 저장)
            """
            CREATE TABLE IF NOT EXISTS config (
              key TEXT PRIMARY KEY,
              val TEXT,
              updated_at INTEGER
            );
            """,

            # events: 시스템 이벤트/로그
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER,
              level TEXT,
              source TEXT,
              message TEXT
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);",

            # patches: 기능 추가 릴리스 로그 (patch.txt와 동기)
            """
            CREATE TABLE IF NOT EXISTS patches (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts INTEGER,
              version TEXT,
              title TEXT,
              note TEXT
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_patches_ts ON patches(ts);",
        ]
        with self._lock, self._conn:
            for q in ddl:
                self._conn.execute(q)
        log.info("[DB] schema ready")

    # ---------- small helpers ----------
    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def record_event(self, level: str, source: str, message: str, ts: Optional[int] = None) -> None:
        ts = ts or self._now_ms()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events(ts, level, source, message) VALUES (?,?,?,?)",
                (ts, level, source, message),
            )

    def save_patch(self, version: str, title: str, note: str = "", ts: Optional[int] = None) -> None:
        ts = ts or self._now_ms()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO patches(ts, version, title, note) VALUES (?,?,?,?)",
                (ts, version, title, note),
            )

    def upsert_config(self, key: str, val: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO config(key, val, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  val=excluded.val,
                  updated_at=excluded.updated_at
                """,
                (key, val, self._now_ms()),
            )

    def get_config(self, key: str) -> Optional[str]:
        with self._lock:
            cur = self._conn.execute("SELECT val FROM config WHERE key=?", (key,))
            row = cur.fetchone()
            return row["val"] if row else None

    def save_trade(self, d: Dict[str, Any]) -> None:
        # 필드 기본값 보정
        row = {
            "ts": int(d.get("ts") or self._now_ms()),
            "symbol": str(d.get("symbol") or ""),
            "side": d.get("side"),
            "qty": float(d.get("qty") or 0.0),
            "px": float(d.get("px") or 0.0),
            "type": d.get("type"),
            "fee": float(d.get("fee") or 0.0),
            "order_id": d.get("order_id"),
            "client_order_id": d.get("client_order_id"),
            "link_id": d.get("link_id"),
        }
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO trades(ts,symbol,side,qty,px,type,fee,order_id,client_order_id,link_id)
                   VALUES (:ts,:symbol,:side,:qty,:px,:type,:fee,:order_id,:client_order_id,:link_id)""",
                row,
            )

    def upsert_position(
        self,
        symbol: str,
        *,
        qty: float = 0.0,
        avg_px: float = 0.0,
        u_pnl: float = 0.0,
        r_pnl: float = 0.0,
        leverage: float = 0.0,
        liq_px: float = 0.0,
        updated_ts: Optional[int] = None,
    ) -> None:
        updated_ts = updated_ts or self._now_ms()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO positions(symbol, qty, avg_px, u_pnl, r_pnl, leverage, liq_px, updated_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  qty=excluded.qty,
                  avg_px=excluded.avg_px,
                  u_pnl=excluded.u_pnl,
                  r_pnl=excluded.r_pnl,
                  leverage=excluded.leverage,
                  liq_px=excluded.liq_px,
                  updated_ts=excluded.updated_ts
                """,
                (symbol, qty, avg_px, u_pnl, r_pnl, leverage, liq_px, updated_ts),
            )
