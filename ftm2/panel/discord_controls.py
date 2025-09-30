from __future__ import annotations

from typing import Callable, Dict
import os
import sqlite3
import time


class ConfigStore:
    def __init__(self, db_path: str = "ftm2.sqlite3") -> None:
        self.db = db_path
        self._init()

    def _init(self) -> None:
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS config (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)"
        )
        con.commit()
        con.close()

    def set(self, k: str, v: str) -> None:
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute(
            "INSERT INTO config(k,v,ts) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts",
            (k, v, int(time.time())),
        )
        con.commit()
        con.close()

    def get(self, k: str, default=None):
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT v FROM config WHERE k=?", (k,))
        row = cur.fetchone()
        con.close()
        return row[0] if row else default

    def dump(self) -> Dict:
        con = sqlite3.connect(self.db)
        cur = con.cursor()
        cur.execute("SELECT k,v FROM config")
        rows = cur.fetchall()
        con.close()
        return {k: v for k, v in rows}


# [ANCHOR:PANEL_TUNER]
class PanelTuner:
    def __init__(self, store: ConfigStore, on_change: Callable[[str, str], None]):
        self.store = store
        self.on_change = on_change

    def _map_key(self, key: str) -> str:
        return {
            "tenkan": "IK_TENKAN",
            "kijun": "IK_KIJUN",
            "sen": "IK_SEN",
            "twist_guard": "IK_TWIST_GUARD",
            "thick_pct": "IK_THICK_PCT",
            "w_imk": "W_IMK",
            "w_trend": "SC_W_TREND",
            "w_mr": "SC_W_MR",
            "w_brk": "SC_W_BRK",
            "gates": "IK_GATES",
            "align": "REGIME_ALIGN_MODE",
            "strategy": "EXEC_STRATEGY",
        }.get(key, key)

    def _apply_set(self, key: str, value):
        env_key = self._map_key(key)
        if not env_key:
            return {"ok": False, "error": "unknown_key"}
        self.store.set(f"env.{env_key}", str(value))
        if callable(self.on_change):
            self.on_change(env_key, str(value))
        return {"ok": True, "key": key, "env": env_key, "value": value}

    def apply_command(self, cmd: str, args: Dict):
        if cmd in {"ik.set", "weights.set", "gates.set", "strategy.set"}:
            return self._apply_set(args.get("key"), args.get("value"))
        if cmd in {"ik.nudge", "+", "-"}:
            key = args.get("key")
            delta = float(args.get("delta", 0))
            env_key = self._map_key(key)
            current = float(self.store.get(f"env.{env_key}", os.getenv(env_key, "0")))
            return self._apply_set(key, current + delta)
        return {"ok": False, "error": "unknown_cmd"}
