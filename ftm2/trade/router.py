# -*- coding: utf-8 -*-
"""
Order Router: targets -> orders (dry-run by default)
- LOT_SIZE step, MIN_NOTIONAL 준수
- cooldown / tolerance
- reduceOnly 처리
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional
import time
import math
import logging

try:
    from ftm2.exchange.binance import BinanceClient
except Exception:
    from exchange.binance import BinanceClient  # type: ignore

log = logging.getLogger("ftm2.exec")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@dataclass
class ExecConfig:
    active: bool = False          # 실주문 on/off
    cooldown_s: float = 5.0       # 심볼별 최소 간격
    tol_rel: float = 0.05         # |delta| < |target|*tol_rel 이면 skip
    tol_abs: float = 0.0          # |delta| < tol_abs 이면 skip
    order_type: str = "MARKET"
    reduce_only: bool = True      # 청산·감소는 reduceOnly

def _round_step(x: float, step: float) -> float:
    if step <= 0: return x
    # binance step은 소수 1e-? 형태가 많음 → 반올림 오차 방지
    k = round(x / step)
    return k * step

# [ANCHOR:ORDER_ROUTER]
class OrderRouter:
    def __init__(self, client: BinanceClient, cfg: ExecConfig) -> None:
        self.cli = client
        self.cfg = cfg
        self._last_sent_ms: Dict[str, int] = {}  # sym -> epoch_ms
        self._meta: Dict[str, Dict[str, float]] = {}  # sym -> {step,min_notional}
        self._warm = False

    # ---- exchange meta ----
    def _ensure_meta(self, symbols: List[str]) -> None:
        if self._warm:
            return
        r = self.cli.exchange_info(symbols)
        if not r.get("ok"):
            log.warning("[EXEC] exchangeInfo 실패: %s", r.get("error"))
            # 기본값 보수적으로
            for s in symbols:
                self._meta.setdefault(s, {"step": 0.001, "min_notional": 5.0})
            self._warm = True
            return
        info = r["data"]
        arr = info.get("symbols") or []
        for si in arr:
            sym = si.get("symbol")
            step = 0.001
            min_notional = 5.0
            for f in si.get("filters", []):
                ft = f.get("filterType")
                if ft == "LOT_SIZE":
                    try:
                        step = float(f.get("stepSize", step))
                    except Exception:
                        pass
                # 선물은 'MIN_NOTIONAL' 또는 'NOTIONAL' 형식이 존재
                if ft in ("MIN_NOTIONAL", "NOTIONAL"):
                    try:
                        mn = f.get("minNotional") or f.get("notional")
                        if mn is not None:
                            min_notional = float(mn)
                    except Exception:
                        pass
                # 일부 선물은 MARKET_LOT_SIZE만 노출되기도 함 → 보조
                if ft == "MARKET_LOT_SIZE" and step == 0.001:
                    try:
                        step = float(f.get("stepSize", step))
                    except Exception:
                        pass
            self._meta[sym] = {"step": step, "min_notional": min_notional}
        self._warm = True

    # ---- plan & send ----
    def _too_soon(self, sym: str) -> bool:
        last = self._last_sent_ms.get(sym, 0)
        return (time.time() * 1000 - last) < (self.cfg.cooldown_s * 1000)

    def _skip_tolerance(self, delta: float, target: float) -> bool:
        if abs(delta) < self.cfg.tol_abs:
            return True
        if abs(target) > 0 and abs(delta) < abs(target) * self.cfg.tol_rel:
            return True
        return False

    def sync(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        symbols = list((snapshot.get("targets") or {}).keys())
        if not symbols:
            return []
        self._ensure_meta(symbols)

        positions = snapshot.get("positions") or {}
        marks = snapshot.get("marks") or {}
        targets = snapshot.get("targets") or {}

        results: List[Dict[str, Any]] = []
        for sym, tgt in targets.items():
            price = float((marks.get(sym) or {}).get("price") or 0.0)
            pos = float((positions.get(sym) or {}).get("pa") or 0.0)  # positionAmt
            target_qty = float(tgt.get("target_qty") or 0.0)
            delta = target_qty - pos

            if self._too_soon(sym):
                results.append({
                    "symbol": sym, "side": "SKIP", "delta_qty": delta, "qty_sent": 0.0,
                    "price": price, "reason": "COOLDOWN", "mode": "DRY" if not self.cfg.active else "LIVE",
                    "result": None
                })
                continue

            if self._skip_tolerance(delta, target_qty):
                results.append({
                    "symbol": sym, "side": "SKIP", "delta_qty": delta, "qty_sent": 0.0,
                    "price": price, "reason": "TOL", "mode": "DRY" if not self.cfg.active else "LIVE",
                    "result": None
                })
                continue

            side = "BUY" if delta > 0 else "SELL"
            step = (self._meta.get(sym) or {}).get("step", 0.001)
            min_notional = (self._meta.get(sym) or {}).get("min_notional", 5.0)
            qty_raw = abs(delta)
            qty = _round_step(qty_raw, step)
            notional = qty * price

            if qty <= 0.0:
                results.append({
                    "symbol": sym, "side": "SKIP", "delta_qty": delta, "qty_sent": 0.0,
                    "price": price, "reason": "STEP_ZERO", "mode": "DRY" if not self.cfg.active else "LIVE",
                    "result": None
                })
                continue
            if price <= 0.0 or notional < min_notional:
                results.append({
                    "symbol": sym, "side": "SKIP", "delta_qty": delta, "qty_sent": 0.0,
                    "price": price, "reason": "MIN_NOTIONAL", "mode": "DRY" if not self.cfg.active else "LIVE",
                    "result": None
                })
                continue

            reduce_only = False
            # 현재 포지션 절대값이 줄어드는 방향이면 reduceOnly
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY") or target_qty == 0.0:
                reduce_only = self.cfg.reduce_only

            payload = {
                "symbol": sym,
                "side": side,
                "type": self.cfg.order_type,
                "quantity": f"{qty:.10f}".rstrip("0").rstrip("."),  # 문자열 권장
            }
            if reduce_only:
                payload["reduceOnly"] = True

            mode = "LIVE" if self.cfg.active else "DRY"
            if not self.cfg.active:
                log.info("[EXEC_DRY] %s %s qty=%s reason=PLAN", sym, side, payload["quantity"])
                self._last_sent_ms[sym] = int(time.time() * 1000)
                results.append({
                    "symbol": sym, "side": side, "delta_qty": delta, "qty_sent": qty,
                    "price": price, "reason": "DRY", "mode": mode, "result": {"ok": True, "dry": True}
                })
                continue

            # 실주문: BinanceClient 가 order_active=False 면 스텁에러가 온다 → 그대로 노출
            r = self.cli.create_order(payload)
            if r.get("ok"):
                log.info("[EXEC] %s %s qty=%s @~%g", sym, side, payload["quantity"], price)
                self._last_sent_ms[sym] = int(time.time() * 1000)
            else:
                log.warning("[EXEC_ERR] %s %s %s", sym, side, r.get("error"))
            results.append({
                "symbol": sym, "side": side, "delta_qty": delta, "qty_sent": qty,
                "price": price, "reason": "SENT" if r.get("ok") else "ERR",
                "mode": mode, "result": r
            })
        return results

    def last_sent_ms(self, sym: str) -> Optional[int]:
        """해당 심볼의 마지막 주문(또는 드라이런 전송) 시각(ms)"""
        return self._last_sent_ms.get(sym)

    def nudge(self, sym: str) -> None:
        """쿨다운을 즉시 해제해 다음 루프에서 바로 재시도 가능하게 함"""
        self._last_sent_ms[sym] = 0
