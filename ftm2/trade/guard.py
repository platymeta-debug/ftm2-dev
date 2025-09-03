# -*- coding: utf-8 -*-
"""
Position Guard
- 최대 레버(총합/심볼별)
- 손실 스톱아웃(% of notional)
- 트레일링 스탑(이익 활성 → 피크 대비 되돌림폭)
- 즉시 reduceOnly MARKET로 강제 평탄/축소 실행
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional
import time
import logging
import math

try:
    from ftm2.core.state import StateBus
    from ftm2.trade.router import OrderRouter
    from ftm2.discord_bot.notify import enqueue_alert
except Exception:  # pragma: no cover
    from core.state import StateBus  # type: ignore
    from trade.router import OrderRouter  # type: ignore
    from discord_bot.notify import enqueue_alert  # type: ignore

log = logging.getLogger("ftm2.guard")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class GuardConfig:
    enabled: bool = True
    max_lever_total: float = 2.5         # 총합 레버리지 한도
    max_lever_per_sym: float = 0.8       # 심볼별 레버리지 한도
    stop_pct: float = 3.0                # 손실 % (노셔널 대비) 초과 시 강제 평탄
    trail_activate_pct: float = 1.0      # 이익 % 도달 시 트레일 시작
    trail_width_pct: float = 0.6         # 피크에서 되돌림폭 %


# [ANCHOR:POSITION_GUARD]
class PositionGuard:
    def __init__(self, bus: StateBus, router: OrderRouter, cfg: GuardConfig = GuardConfig()) -> None:
        self.bus = bus
        self.router = router
        self.cfg = cfg
        self._trail_active: Dict[str, bool] = {}
        self._trail_peak: Dict[str, float] = {}
        self._last_T: int = 0

    # ---- helpers ----
    def _equity(self, snapshot: Dict[str, Any]) -> float:
        acc = snapshot.get("account") or {}
        for k in ("totalWalletBalance", "totalCrossWalletBalance", "availableBalance"):
            v = acc.get(k)
            if v is not None:
                try:
                    return max(0.0, float(v))
                except Exception:
                    pass
        return 1000.0

    def _notional(self, qty: float, price: float) -> float:
        return abs(qty) * max(0.0, price)

    def _lever(self, qty: float, price: float, equity: float) -> float:
        if equity <= 0.0:
            return 0.0
        return self._notional(qty, price) / equity

    def _force_flat(self, sym: str, pos_qty: float) -> dict:
        try:
            r = self.router.force_flat(sym, qty=abs(pos_qty))
        except Exception as e:  # pragma: no cover
            r = {"ok": False, "error": str(e)}
        return r

    def _force_reduce_to(self, sym: str, pos_qty: float, target_abs_qty: float) -> dict:
        try:
            r = self.router.force_reduce_to(sym, target_abs_qty=target_abs_qty)
        except Exception as e:  # pragma: no cover
            r = {"ok": False, "error": str(e)}
        return r

    # ---- core checks ----
    def _check_leverage(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        positions = snapshot.get("positions") or {}
        marks = snapshot.get("marks") or {}
        eq = self._equity(snapshot)

        for sym, p in positions.items():
            qty = float(p.get("pa") or 0.0)
            if qty == 0.0:
                self._trail_active.pop(sym, None)
                self._trail_peak.pop(sym, None)
                continue
            price = float((marks.get(sym) or {}).get("price") or 0.0)
            lv = self._lever(qty, price, eq)
            if lv > self.cfg.max_lever_per_sym > 0.0:
                allowed_notional = eq * self.cfg.max_lever_per_sym
                target_abs_qty = allowed_notional / price if price > 0.0 else 0.0
                r = self._force_reduce_to(sym, qty, target_abs_qty)
                actions.append({
                    "symbol": sym,
                    "action": "REDUCE_TO_CAP",
                    "reason": "LEVER_PER_SYM",
                    "qty": abs(qty) - target_abs_qty,
                    "price": price,
                    "details": {"lv": lv},
                    "result": r,
                })
                log.warning(
                    "[GUARD][REDUCE] %s lever=%.3f > per_sym=%.3f",
                    sym,
                    lv,
                    self.cfg.max_lever_per_sym,
                )

        cur_total = sum(
            self._notional(float(p.get("pa") or 0.0), float((marks.get(s) or {}).get("price") or 0.0))
            for s, p in positions.items()
        )
        cur_lv = cur_total / eq if eq > 0.0 else 0.0
        if cur_lv > self.cfg.max_lever_total > 0.0 and cur_total > 0.0:
            s = self.cfg.max_lever_total / cur_lv
            for sym, p in positions.items():
                qty = float(p.get("pa") or 0.0)
                if qty == 0.0:
                    continue
                price = float((marks.get(sym) or {}).get("price") or 0.0)
                cur_abs = abs(qty)
                target_abs_qty = cur_abs * s
                if target_abs_qty < cur_abs:
                    r = self._force_reduce_to(sym, qty, target_abs_qty)
                    actions.append({
                        "symbol": sym,
                        "action": "REDUCE_TO_TOTAL_CAP",
                        "reason": "LEVER_TOTAL",
                        "qty": cur_abs - target_abs_qty,
                        "price": price,
                        "details": {"cur_lv": cur_lv, "s": s},
                        "result": r,
                    })
            log.warning(
                "[GUARD][REDUCE] total lever=%.3f > max=%.3f → scale=%.3f",
                cur_lv,
                self.cfg.max_lever_total,
                s,
            )
        return actions

    def _check_stopout(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        positions = snapshot.get("positions") or {}
        marks = snapshot.get("marks") or {}
        for sym, p in positions.items():
            qty = float(p.get("pa") or 0.0)
            if qty == 0.0:
                self._trail_active.pop(sym, None)
                self._trail_peak.pop(sym, None)
                continue
            ep = float(p.get("ep") or 0.0)
            price = float((marks.get(sym) or {}).get("price") or 0.0)
            notional = self._notional(qty, ep if ep > 0 else price)
            upnl = float(p.get("up") or 0.0)
            if notional <= 0.0:
                continue
            pnl_pct = (upnl / notional) * 100.0
            if pnl_pct <= -abs(self.cfg.stop_pct):
                r = self._force_flat(sym, pos_qty=qty)
                actions.append({
                    "symbol": sym,
                    "action": "FORCE_FLAT",
                    "reason": "STOPOUT",
                    "qty": abs(qty),
                    "price": price,
                    "details": {"pnl_pct": pnl_pct},
                    "result": r,
                })
                log.warning(
                    "[GUARD][FLAT] %s stopout pnl=%.2f%% ≤ -%.2f%%",
                    sym,
                    pnl_pct,
                    self.cfg.stop_pct,
                )
                try:
                    enqueue_alert(
                        f"🛑 스톱아웃 — {sym} 미실현손실 {pnl_pct:.2f}% 초과 → 즉시 평탄",
                        intent="logs",
                    )
                except Exception:
                    pass
        return actions

    def _check_trailing(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        positions = snapshot.get("positions") or {}
        marks = snapshot.get("marks") or {}
        for sym, p in positions.items():
            qty = float(p.get("pa") or 0.0)
            if qty == 0.0:
                self._trail_active.pop(sym, None)
                self._trail_peak.pop(sym, None)
                continue
            ep = float(p.get("ep") or 0.0)
            price = float((marks.get(sym) or {}).get("price") or 0.0)
            upnl = float(p.get("up") or 0.0)
            notional = self._notional(qty, ep if ep > 0 else price)
            pnl_pct = (upnl / notional) * 100.0 if notional > 0 else 0.0

            if not self._trail_active.get(sym, False) and pnl_pct >= abs(self.cfg.trail_activate_pct):
                self._trail_active[sym] = True
                self._trail_peak[sym] = price
                log.info("[GUARD] trail activate %s at pnl=%.2f%%", sym, pnl_pct)

            if not self._trail_active.get(sym, False):
                continue

            side_long = qty > 0
            peak = self._trail_peak.get(sym, price)
            if side_long:
                if price > peak:
                    self._trail_peak[sym] = price
                    peak = price
            else:
                if price < peak or peak == 0:
                    self._trail_peak[sym] = price
                    peak = price

            if peak <= 0.0 or price <= 0.0:
                continue
            pull_pct = ((peak - price) / peak * 100.0) if side_long else ((price - peak) / peak * 100.0)
            if pull_pct >= abs(self.cfg.trail_width_pct):
                r = self._force_flat(sym, pos_qty=qty)
                actions.append({
                    "symbol": sym,
                    "action": "FORCE_FLAT",
                    "reason": "TRAIL_STOP",
                    "qty": abs(qty),
                    "price": price,
                    "details": {"pull_pct": pull_pct, "peak": peak},
                    "result": r,
                })
                log.warning(
                    "[GUARD][FLAT] %s trail stop pull=%.2f%% ≥ %.2f%%",
                    sym,
                    pull_pct,
                    self.cfg.trail_width_pct,
                )
                try:
                    enqueue_alert(
                        f"🟨 트레일링 스탑 — {sym} 되돌림 {pull_pct:.2f}% → 평탄",
                        intent="logs",
                    )
                except Exception:
                    pass
                self._trail_active.pop(sym, None)
                self._trail_peak.pop(sym, None)
        return actions

    # ---- public ----
    def process(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.cfg.enabled:
            return []
        actions: List[Dict[str, Any]] = []
        actions += self._check_leverage(snapshot)
        actions += self._check_stopout(snapshot)
        actions += self._check_trailing(snapshot)
        return actions

