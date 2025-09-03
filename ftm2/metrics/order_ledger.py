# -*- coding: utf-8 -*-
"""
Order Ledger
- 주문 제출/업데이트를 DB에 기록하고 롤링 윈도우 통계를 산출
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple
import time, math, logging

try:
    from ftm2.core.persistence import Persistence
except Exception:
    from core.persistence import Persistence  # type: ignore

log = logging.getLogger("ftm2.ledger")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class OLConfig:
    window_sec: int = 600
    report_sec: int = 60
    min_orders: int = 5


# [ANCHOR:ORDER_LEDGER]
class OrderLedger:
    def __init__(self, db: Persistence, cfg: OLConfig = OLConfig()) -> None:
        self.db = db
        self.cfg = cfg
        self._last_report_ms = 0

    # -------- ingest --------
    def on_submit(self, data: Dict[str, Any]) -> None:
        """
        data keys (best effort):
          ts_submit(ms), symbol, side, type, price(opt), orig_qty, mode(DRY/LIVE), reduce_only(bool),
          client_order_id(opt), order_id(opt)
        """
        rec = {
            "ts_submit": int(data.get("ts_submit") or int(time.time() * 1000)),
            "symbol": (data.get("symbol") or "").upper(),
            "side": (data.get("side") or "").upper(),
            "type": (data.get("type") or "MARKET").upper(),
            "price": float(data.get("price") or 0.0),
            "orig_qty": float(data.get("orig_qty") or 0.0),
            "mode": str(data.get("mode") or "DRY"),
            "reduce_only": 1 if bool(data.get("reduce_only")) else 0,
            "client_order_id": data.get("client_order_id"),
            "order_id": str(data.get("order_id") or "") or None,
        }
        try:
            self.db.save_order_submit(rec)
            log.debug("[LEDGER] submit %s %s %s %s", rec["symbol"], rec["side"], rec["type"], rec["orig_qty"])
        except Exception as e:
            log.warning("[LEDGER] submit err: %s", e)

    def on_update(self, ev: Dict[str, Any]) -> None:
        """
        ev from ORDER_TRADE_UPDATE normalized fields:
          ts, symbol, status, side, lastQty, lastPrice, cumQty(executed), avgPrice, orderId, clientOrderId
        """
        rec = {
            "order_id": str(ev.get("orderId") or ""),
            "ts": int(ev.get("ts") or int(time.time() * 1000)),
            "status": (ev.get("status") or ev.get("X") or "").upper(),
            "last_qty": float(ev.get("lastQty") or 0.0),
            "last_price": float(ev.get("lastPrice") or 0.0),
            "executed_qty": float(ev.get("cumQty") or ev.get("executedQty") or 0.0),
            "avg_price": float(ev.get("avgPrice") or 0.0),
            "symbol": (ev.get("symbol") or "").upper(),
        }
        try:
            self.db.save_order_event(rec)
            log.debug("[LEDGER] update %s %s exe=%.10f", rec["symbol"], rec["status"], rec["executed_qty"])
        except Exception as e:
            log.warning("[LEDGER] update err: %s", e)

    # -------- report --------
    def _fetch_window(self, window_sec: int) -> List[Dict[str, Any]]:
        start_ms = int(time.time() * 1000) - window_sec * 1000
        return self.db.fetch_orders_since(start_ms)

    def summary(self, window_sec: Optional[int] = None) -> Dict[str, Any]:
        ws = int(window_sec or self.cfg.window_sec)
        rows = self._fetch_window(ws)
        if not rows:
            return {"window_sec": ws, "orders": 0}
        # 집계
        per_sym: Dict[str, Dict[str, Any]] = {}
        total = {"orders": 0, "filled": 0, "cancelled": 0, "avg_ttf_ms": 0.0, "p50_ttf_ms": 0.0}
        ttf_pool: List[int] = []

        for r in rows:
            sym = r["symbol"]
            d = per_sym.setdefault(sym, {"orders": 0, "filled": 0, "cancelled": 0, "avg_ttf_ms": 0.0, "ttf_list": []})
            d["orders"] += 1
            total["orders"] += 1
            st = (r.get("last_status") or "").upper()
            if st == "FILLED" and r.get("ts_filled"):
                d["filled"] += 1
                total["filled"] += 1
                ttf = int(r["ts_filled"]) - int(r["ts_submit"])
                d["ttf_list"].append(ttf)
                ttf_pool.append(ttf)
            if st in ("CANCELED", "EXPIRED", "REJECTED"):
                d["cancelled"] += 1
                total["cancelled"] += 1

        # TTF 평균/중앙
        import statistics
        if ttf_pool:
            total["avg_ttf_ms"] = float(statistics.mean(ttf_pool))
            total["p50_ttf_ms"] = float(statistics.median(ttf_pool))
        for sym, d in per_sym.items():
            if d["ttf_list"]:
                d["avg_ttf_ms"] = float(statistics.mean(d["ttf_list"]))
                d["p50_ttf_ms"] = float(statistics.median(d["ttf_list"]))
                del d["ttf_list"]
            else:
                d["avg_ttf_ms"] = 0.0
                d["p50_ttf_ms"] = 0.0

        total["fill_rate"] = (total["filled"] / total["orders"]) if total["orders"] > 0 else 0.0
        total["cancel_rate"] = (total["cancelled"] / total["orders"]) if total["orders"] > 0 else 0.0

        return {
            "window_sec": ws,
            "orders": total["orders"],
            "filled": total["filled"],
            "fill_rate": total["fill_rate"],
            "cancel_rate": total["cancel_rate"],
            "avg_ttf_ms": total["avg_ttf_ms"],
            "p50_ttf_ms": total["p50_ttf_ms"],
            "symbols": per_sym,
            "ts": int(time.time() * 1000),
        }


# 싱글톤 핼퍼
_LEDGER_SINGLETON: Optional[OrderLedger] = None


def get_order_ledger(db: Persistence, cfg: Optional[OLConfig] = None) -> OrderLedger:
    global _LEDGER_SINGLETON
    if _LEDGER_SINGLETON is None:
        _LEDGER_SINGLETON = OrderLedger(db, cfg or OLConfig())
    return _LEDGER_SINGLETON

