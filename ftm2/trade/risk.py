# -*- coding: utf-8 -*-
"""
Risk Engine
- ATR-unit sizing
- Correlation cap (per side)
- Daily loss cut
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional
import time
import math
import logging

log = logging.getLogger("ftm2.risk")
if not log.handlers:  # pragma: no cover - basic config for direct runs
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class RiskConfig:
    risk_target_pct: float = 0.30           # per-position risk budget (of equity)
    corr_cap_per_side: float = 0.65         # side-wise exposure cap (notional/equity)
    day_max_loss_pct: float = 3.0           # daily max loss %
    atr_k: float = 2.0                      # stop distance = k * ATR
    min_notional: float = 20.0              # ignore targets below this (USDT)
    equity_override: Optional[float] = None # 테스트/키없음 환경 보정


# [ANCHOR:RISK_ENGINE]
class RiskEngine:
    def __init__(self, symbols: List[str], cfg: RiskConfig = RiskConfig()) -> None:
        self.symbols = symbols
        self.cfg = cfg
        self._last_T: Dict[str, int] = {}
        self._day_cut_on: bool = False
        self._last_cut_state_sent: Optional[bool] = None

    # ---- helpers ----
    def _equity(self, snapshot: Dict[str, Any]) -> float:
        acc = snapshot.get("account") or {}
        eq = None
        for k in ("totalWalletBalance", "totalCrossWalletBalance", "availableBalance"):
            v = acc.get(k)
            if v is not None:
                try:
                    eq = float(v)
                    break
                except Exception:
                    pass
        if eq is None:
            if self.cfg.equity_override is not None:
                eq = float(self.cfg.equity_override)
            else:
                eq = 1000.0
        return max(0.0, float(eq))

    def _strength(self, score: float, strong_thr: float = 0.60) -> float:
        s = min(1.0, abs(score) / (strong_thr if strong_thr > 1e-9 else 1.0))
        return s

    def _day_pnl_pct(self, snapshot: Dict[str, Any]) -> float:
        rsk = snapshot.get("risk") or {}
        v = rsk.get("day_pnl_pct")
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def process_snapshot(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        닫힌 봉/예측 업데이트에 맞춰 타깃 산출.
        반환 리스트는 심볼별 0..1개의 타깃 dict.
        """
        out: List[Dict[str, Any]] = []
        forecasts: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("forecasts", {})
        features: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("features", {})
        klines: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("klines", {})
        marks: Dict[str, Dict[str, Any]] = snapshot.get("marks", {})

        equity = self._equity(snapshot)
        day_pnl_pct = self._day_pnl_pct(snapshot)
        self._day_cut_on = bool(day_pnl_pct <= -abs(self.cfg.day_max_loss_pct))

        for (sym, itv), fc in forecasts.items():
            bar = klines.get((sym, itv))
            if not bar or not bar.get("x"):
                continue
            T = int(bar.get("T") or 0)
            if self._last_T.get(sym) == T:
                continue
            self._last_T[sym] = T

            side = fc.get("stance") or "FLAT"
            score = float(fc.get("score", 0.0))
            feats = features.get((sym, itv)) or {}
            atr = float(feats.get("atr14", 0.0))
            price = float((marks.get(sym) or {}).get("price") or feats.get("c") or 0.0)

            tgt_qty = 0.0
            reason = "FLAT"
            strength = self._strength(score)

            if self._day_cut_on:
                reason = "DAY_CUT"
            elif side == "FLAT" or atr <= 0.0 or price <= 0.0:
                reason = "NO_SIGNAL" if side == "FLAT" else "NO_ATR_OR_PRICE"
            else:
                dollar_risk_per_unit = max(1e-9, self.cfg.atr_k * atr)
                budget = equity * max(0.0, self.cfg.risk_target_pct) * strength
                qty = budget / dollar_risk_per_unit
                if qty * price < self.cfg.min_notional:
                    reason = "MIN_NOTIONAL"
                    qty = 0.0
                else:
                    if side == "SHORT":
                        qty = -qty
                    reason = "OK"
                tgt_qty = float(qty)

            tgt_notional = abs(tgt_qty) * price
            out.append({
                "symbol": sym,
                "side": side,
                "target_qty": tgt_qty,
                "target_notional": tgt_notional,
                "reason": reason,
                "inputs": {
                    "equity": equity,
                    "price": price,
                    "atr": atr,
                    "atr_k": self.cfg.atr_k,
                    "score": score,
                    "strength": strength,
                    "risk_target_pct": self.cfg.risk_target_pct,
                },
                "ts": int(time.time() * 1000),
            })

        if out:
            cap_usdt = equity * max(0.0, min(1.0, self.cfg.corr_cap_per_side))
            sum_long = sum(t["target_notional"] for t in out if t["target_qty"] > 0)
            sum_short = sum(t["target_notional"] for t in out if t["target_qty"] < 0)

            def _cap(side_sum: float) -> float:
                if side_sum <= cap_usdt or side_sum <= 0.0:
                    return 1.0
                return cap_usdt / side_sum

            scl_long = _cap(sum_long)
            scl_short = _cap(sum_short)
            if scl_long < 0.999 or scl_short < 0.999:
                log.info(
                    "[RISK_CAP] apply long=%.3f short=%.3f (cap=%.2f eq=%.2fK)",
                    scl_long,
                    scl_short,
                    cap_usdt,
                    equity / 1000.0,
                )
                for t in out:
                    if t["target_qty"] > 0:
                        t["target_qty"] *= scl_long
                        t["target_notional"] *= scl_long
                        t["reason"] = "CAP" if t["reason"] == "OK" else t["reason"]
                    elif t["target_qty"] < 0:
                        t["target_qty"] *= scl_short
                        t["target_notional"] *= scl_short
                        t["reason"] = "CAP" if t["reason"] == "OK" else t["reason"]

        return out

    @property
    def day_cut_on(self) -> bool:
        return self._day_cut_on
