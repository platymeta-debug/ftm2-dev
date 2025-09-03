# -*- coding: utf-8 -*-
"""
Open Orders Manager
- 정기 조회 + 정책 취소(드리프트/스테일/데일리컷/초과건수)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import time
import logging

try:
    from ftm2.exchange.binance import BinanceClient
    from ftm2.core.state import StateBus
    from ftm2.trade.router import OrderRouter
    from ftm2.metrics.exec_quality import get_exec_quality
except Exception:  # pragma: no cover
    from exchange.binance import BinanceClient  # type: ignore
    from core.state import StateBus  # type: ignore
    from trade.router import OrderRouter  # type: ignore
    from metrics.exec_quality import get_exec_quality  # type: ignore


log = logging.getLogger("ftm2.openorders")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class OOConfig:
    enabled: bool = True
    poll_s: float = 3.0
    stale_secs: float = 45.0
    price_drift_pct: float = 0.004   # 0.4% 이상 괴리면 취소
    cancel_on_day_cut: bool = True
    max_open_per_sym: int = 2


# [ANCHOR:OO_MANAGER]
class OpenOrdersManager:
    def __init__(self, client: BinanceClient, bus: StateBus, router: OrderRouter, cfg: OOConfig = OOConfig()) -> None:
        self.cli = client
        self.bus = bus
        self.router = router
        self.cfg = cfg

    # ---- REST 어댑터 ----
    def _fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        클라이언트 구현 차이를 흡수: get_open_orders / open_orders / list_open_orders
        선물 기준: 심볼 지정 시 해당 심볼만, 없으면 전체 반환.
        표준화 필드:
          symbol, orderId, side, type, status, price, origQty, executedQty, time, updateTime
        """
        resp = None
        for name in ("get_open_orders", "open_orders", "list_open_orders", "getOpenOrders"):
            if hasattr(self.cli, name):
                try:
                    resp = getattr(self.cli, name)(symbol=symbol) if symbol else getattr(self.cli, name)()
                    break
                except TypeError:
                    try:
                        resp = getattr(self.cli, name)(symbol) if symbol else getattr(self.cli, name)()
                        break
                    except Exception:
                        pass
                except Exception:
                    pass
        if not resp or not isinstance(resp, dict) or not resp.get("ok"):
            return []
        raw = resp.get("data") or []
        out: List[Dict[str, Any]] = []
        for o in raw:
            try:
                out.append({
                    "symbol": (o.get("symbol") or o.get("s") or "").upper(),
                    "orderId": str(o.get("orderId") or o.get("i")),
                    "side": (o.get("side") or o.get("S") or "").upper(),
                    "type": (o.get("type") or o.get("o") or "").upper(),
                    "status": (o.get("status") or o.get("X") or "").upper(),
                    "price": float(o.get("price") or o.get("p") or 0.0),
                    "origQty": float(o.get("origQty") or o.get("q") or 0.0),
                    "executedQty": float(o.get("executedQty") or o.get("z") or 0.0),
                    "time": int(o.get("time") or o.get("T") or o.get("transactTime") or 0),
                    "updateTime": int(o.get("updateTime") or o.get("E") or o.get("time") or 0),
                })
            except Exception:
                continue
        return out

    def _cancel(self, sym: str, order_id: Optional[str], reason: str, results: List[Dict[str, Any]]) -> None:
        r = self.router.cancel_open_orders(sym, order_id=order_id)
        if r.get("ok"):
            log.info("[OO][CANCEL] %s oid=%s reason=%s", sym, order_id, reason)
        else:
            log.warning("[OO][CANCEL] 실패 %s oid=%s err=%s", sym, order_id, r.get("error"))
        results.append({"symbol": sym, "orderId": order_id, "reason": reason, "ok": r.get("ok")})

        try:
            get_exec_quality().ingest_cancels(1)
        except Exception:
            pass


    # ---- 메인 ----
    def poll_once(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if not self.cfg.enabled:
            return {"open_count": 0, "cancelled": []}

        # 데일리컷: 모든 심볼 취소
        if self.cfg.cancel_on_day_cut and (snapshot.get("risk") or {}).get("day_cut"):
            cancelled: List[Dict[str, Any]] = []
            symbols = set((snapshot.get("targets") or {}).keys()) | set((snapshot.get("positions") or {}).keys()) | set((snapshot.get("marks") or {}).keys())
            for s in symbols:
                self._cancel(s, None, "DAY_CUT", cancelled)
            self.bus.set_open_orders({})
            return {"open_count": 0, "cancelled": cancelled}

        # 1) 조회
        oo = self._fetch_open_orders()
        # 2) StateBus 반영
        by_sym: Dict[str, List[Dict[str, Any]]] = {}
        for o in oo:
            by_sym.setdefault(o["symbol"], []).append(o)
        self.bus.set_open_orders(by_sym)

        # 3) 정책 적용
        marks: Dict[str, Dict[str, Any]] = snapshot.get("marks") or {}
        now_ms = int(snapshot.get("now_ts") or time.time() * 1000)
        cancelled: List[Dict[str, Any]] = []

        for sym, items in by_sym.items():
            items_sorted = sorted(items, key=lambda x: x.get("updateTime", 0), reverse=True)
            if self.cfg.max_open_per_sym > 0 and len(items_sorted) > self.cfg.max_open_per_sym:
                for o in items_sorted[self.cfg.max_open_per_sym:]:
                    self._cancel(sym, o.get("orderId"), "MAX_OPEN_PER_SYM", cancelled)

            mark = float((marks.get(sym) or {}).get("price") or 0.0)
            for o in items_sorted[: self.cfg.max_open_per_sym or len(items_sorted)]:
                age_s = max(0.0, (now_ms - int(o.get("updateTime") or o.get("time") or 0)) / 1000.0)
                if age_s >= self.cfg.stale_secs:
                    self._cancel(sym, o.get("orderId"), "STALE", cancelled)
                    continue
                typ = o.get("type", "")
                if typ in ("LIMIT", "LIMIT_MAKER", "STOP", "STOP_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"):
                    op = float(o.get("price") or 0.0)
                    if op > 0.0 and mark > 0.0:
                        drift = abs(op - mark) / mark
                        if drift >= self.cfg.price_drift_pct:
                            self._cancel(sym, o.get("orderId"), f"DRIFT({drift:.3%})", cancelled)

        return {"open_count": len(oo), "cancelled": cancelled}
