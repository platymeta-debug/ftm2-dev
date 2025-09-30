from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, List
import time
import statistics as stats

from ftm2.db.core import get_conn


# [ANCHOR:KPI_PIPE]
class KPIEngine:
    def __init__(self) -> None:
        self.counters = {
            "order_attempt": 0,
            "order_sent": 0,
            "order_filled": 0,
            "order_canceled": 0,
        }
        self.slippages_bps: List[float] = []
        self.ttf_ms: List[float] = []
        self.pnl_daily = 0.0
        self.last_sent_ts: Dict[str, float] = {}

    def on_event(self, evt: Dict) -> Dict:
        et = evt.get("type")
        now = time.time() * 1000
        if et == "order_attempt":
            self.counters["order_attempt"] += 1
        elif et == "order_sent":
            self.counters["order_sent"] += 1
            link_id = evt.get("link_id")
            if link_id:
                self.last_sent_ts[link_id] = now
        elif et == "order_filled":
            self.counters["order_filled"] += 1
            link_id = evt.get("link_id")
            if link_id and link_id in self.last_sent_ts:
                self.ttf_ms.append(now - self.last_sent_ts.pop(link_id))
            if "slippage_bps" in evt:
                try:
                    self.slippages_bps.append(abs(float(evt["slippage_bps"])))
                except Exception:
                    pass
        elif et == "order_canceled":
            self.counters["order_canceled"] += 1
        elif et == "pnl_update":
            try:
                self.pnl_daily = float(evt.get("pnl_daily", self.pnl_daily))
            except Exception:
                pass
        return self.snapshot()

    def snapshot(self) -> Dict:
        slp = stats.mean(self.slippages_bps) if self.slippages_bps else 0.0
        ttf = stats.median(self.ttf_ms) if self.ttf_ms else 0.0
        fill_rate = self.counters["order_filled"] / max(1, self.counters["order_sent"])
        cancel_rate = self.counters["order_canceled"] / max(1, self.counters["order_attempt"])
        return {
            "pnl_daily": round(self.pnl_daily, 2),
            "orders": dict(self.counters),
            "exec_quality": {
                "slippage_bps_avg": round(slp, 2),
                "ttf_ms_p50": round(ttf, 1),
                "fill_rate": round(fill_rate, 3),
                "cancel_rate": round(cancel_rate, 3),
            },
        }


# ---- Legacy KPI helpers kept for compatibility ----


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
            rows = conn.execute(
                "SELECT id, readiness, created_ts FROM tickets WHERE created_ts>=?",
                (now - 3600,),
            ).fetchall()
            total = len(rows)
            ready_cnt = sum(1 for r in rows if (r[1] or "").upper() == "READY")
            exec_cnt = 0
            ttf_ms = 0.0
            for r in rows:
                oid = conn.execute(
                    "SELECT ts_filled FROM orders WHERE link_id=? AND ts_filled IS NOT NULL ORDER BY ts_filled ASC LIMIT 1",
                    (r[0],),
                ).fetchone()
                if oid and oid[0]:
                    exec_cnt += 1
                    try:
                        ttf_ms += max(0, int(oid[0]) - int(float(r[2]) * 1000))
                    except Exception:
                        pass
            return {
                "count": total,
                "ready_rate": (ready_cnt / total * 100.0) if total else 0.0,
                "exec_rate": (exec_cnt / total * 100.0) if total else 0.0,
                "avg_ttf_ms": (ttf_ms / exec_cnt) if exec_cnt else 0.0,
            }
        except Exception:
            return {"count": 0, "ready_rate": 0.0, "exec_rate": 0.0, "avg_ttf_ms": 0.0}

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
