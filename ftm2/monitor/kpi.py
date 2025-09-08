# -*- coding: utf-8 -*-
"""KPI snapshot utilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple
import time
from ftm2.db.core import get_conn


# [ANCHOR:KPI_REPORTER]
@dataclass
class Exposure:
    long_actual: float
    short_actual: float
    long_target: float
    short_target: float


def _calc_exposure_actual(snap, equity: float) -> Tuple[float, float]:
    equity = max(equity, 1e-9)
    long_notional = 0.0
    short_notional = 0.0
    marks = {k: v.get("price") for k, v in (snap.get("marks") or {}).items()}
    for pos in (snap.get("positions") or {}).values():
        sym = pos.get("symbol")
        px = marks.get(sym)
        if px is None:
            continue
        amt = float(pos.get("positionAmt") or 0.0)
        notion = abs(amt) * px
        if amt >= 0:
            long_notional += notion
        else:
            short_notional += notion
    return long_notional / equity, short_notional / equity


def _calc_exposure_target(snap) -> Tuple[float, float]:
    r = snap.get("risk") or {}
    return float(r.get("used_long_ratio", 0.0)), float(r.get("used_short_ratio", 0.0))


def _get_day_e0(state) -> float:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    import os
    try:
        tz = ZoneInfo(os.getenv("DAY_PNL_TZ", "Asia/Seoul"))
    except Exception:
        tz = timezone.utc
    key = f"day_e0_{datetime.now(tz).strftime('%Y-%m-%d')}"
    return float(state.config.get(key) or 0.0)


def compute_kpi_snapshot(state) -> Dict:
    snap = state.snapshot()
    mon = snap.get("monitor") or {}
    equity = float(mon.get("equity") or 0.0)
    day_e0 = _get_day_e0(state)
    day_pnl_pct = 0.0
    if day_e0 > 0:
        day_pnl_pct = (equity - day_e0) / day_e0 * 100.0

    l_t, s_t = _calc_exposure_target(snap)
    l_a, s_a = _calc_exposure_actual(snap, equity)
    if l_t == 0.0 and s_t == 0.0:
        long_pct, short_pct = l_a, s_a
    else:
        long_pct, short_pct = l_t, s_t

    total_notional = 0.0
    for pos in (snap.get("positions") or {}).values():
        px = (snap.get("marks") or {}).get(pos.get("symbol"), {}).get("price")
        if px:
            total_notional += abs(float(pos.get("positionAmt") or 0.0)) * px
    port_lev = total_notional / max(equity, 1e-9)

    def _amt_stats() -> Dict:
        try:
            conn = get_conn()
            now = time.time()
            rows = conn.execute("SELECT id, readiness, created_ts FROM tickets WHERE created_ts>=?", (now-3600,)).fetchall()
            total = len(rows)
            ready_cnt = sum(1 for r in rows if (r[1] or "").upper() == "READY")
            exec_cnt = 0
            ttf_ms = 0.0
            for r in rows:
                oid = conn.execute("SELECT ts_filled FROM orders WHERE link_id=? AND ts_filled IS NOT NULL ORDER BY ts_filled ASC LIMIT 1", (r[0],)).fetchone()
                if oid and oid[0]:
                    exec_cnt += 1
                    try:
                        ttf_ms += max(0, int(oid[0]) - int(float(r[2]) * 1000))
                    except Exception:
                        pass
            return {
                "count": total,
                "ready_rate": (ready_cnt/total*100.0) if total else 0.0,
                "exec_rate": (exec_cnt/total*100.0) if total else 0.0,
                "avg_ttf_ms": (ttf_ms/exec_cnt) if exec_cnt else 0.0,
            }
        except Exception:
            return {"count":0,"ready_rate":0.0,"exec_rate":0.0,"avg_ttf_ms":0.0}

    kpi = {
        "equity": equity,
        "day_pnl_pct": day_pnl_pct,
        "exposure": {
            "long_pct": long_pct,
            "short_pct": short_pct,
            "long_actual": l_a,
            "short_actual": s_a,
            "long_target": l_t,
            "short_target": s_t,
        },
        "port_leverage": port_lev,
        "amt": _amt_stats(),
    }
    mon["kpi"] = kpi
    state.set_monitor_state(mon)
    return kpi

