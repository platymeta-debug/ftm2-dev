from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
import os
import time


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except Exception:
        return default


RISK_TARGET_PCT = _env_float("RISK_TARGET_PCT", 0.30)
RISK_ATR_LOOKBACK = _env_int("RISK_ATR_LOOKBACK", 14)
RISK_MIN_NOTIONAL = _env_float("RISK_MIN_NOTIONAL", 50.0)
RISK_MAX_NOTIONAL = _env_float("RISK_MAX_NOTIONAL", 2500.0)
CORR_LOOKBACK = _env_int("CORR_LOOKBACK", 240)
CORR_CAP_PER_SIDE = _env_float("CORR_CAP_PER_SIDE", 0.65)
DAILY_MAX_LOSS_PCT = _env_float("DAILY_MAX_LOSS_PCT", 0.03)
DAILY_COOLDOWN_MIN = _env_int("DAILY_COOLDOWN_MIN", 60)


@dataclass
class RiskResult:
    qty: float
    notional: float
    r_unit: float
    reason: str
    caps: Dict[str, float]


class RiskEngine:
    """Risk sizing helper."""

    def __init__(self) -> None:
        self._day_anchor: str | None = None
        self._daily_loss_cut = False
        self._daily_cut_ts = 0.0

    def _equity(self, account: Dict) -> float:
        usdt = 0.0
        for bal in account.get("balances", []):
            if (bal.get("asset") or "").upper() == "USDT":
                try:
                    usdt = float(bal.get("wb", 0.0))
                except Exception:
                    usdt = 0.0
                break
        return max(0.0, usdt)

    def _returns(self, closes: List[float]) -> List[float]:
        out: List[float] = []
        prev = None
        for price in closes:
            if prev is None or prev == 0.0:
                out.append(0.0)
            else:
                out.append(price / prev - 1.0)
            prev = price
        return out

    def _corr(self, ret_a: List[float], ret_b: List[float]) -> float:
        n = min(len(ret_a), len(ret_b))
        if n < 5:
            return 0.0
        a = ret_a[-n:]
        b = ret_b[-n:]
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        var_a = sum((x - mean_a) ** 2 for x in a)
        var_b = sum((y - mean_b) ** 2 for y in b)
        if var_a == 0.0 or var_b == 0.0:
            return 0.0
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        return max(-1.0, min(1.0, cov / (var_a * var_b) ** 0.5))

    def _calc_corr_cap(self, symbol: str, kline_map: Dict[str, Dict[str, List[dict]]]) -> float:
        ref = "BTCUSDT"
        if symbol == ref or ref not in kline_map or symbol not in kline_map:
            return 1.0

        def _closes(sym: str, tf: str = "15m", size: int = CORR_LOOKBACK) -> List[float]:
            arr = kline_map.get(sym, {}).get(tf, [])
            return [float(x.get("c", 0.0)) for x in arr][-size:]

        rho = self._corr(self._returns(_closes(symbol)), self._returns(_closes(ref)))
        cap = 1.0 - max(0.0, rho)
        return max(CORR_CAP_PER_SIDE, cap)

    def _check_daily_cut(self, pnl_daily: float, equity: float) -> Tuple[bool, str]:
        day = time.strftime("%Y-%m-%d", time.localtime())
        if self._day_anchor != day:
            self._day_anchor = day
            self._daily_loss_cut = False
            self._daily_cut_ts = 0.0

        if self._daily_loss_cut:
            if time.time() - self._daily_cut_ts < DAILY_COOLDOWN_MIN * 60:
                return True, "daily_cooldown"
            self._daily_loss_cut = False

        if equity <= 0:
            return False, ""
        if pnl_daily < -DAILY_MAX_LOSS_PCT * equity:
            self._daily_loss_cut = True
            self._daily_cut_ts = time.time()
            return True, "daily_loss_cut"
        return False, ""

    def size_order(
        self,
        symbol: str,
        side: str,
        features: Dict,
        regime: Dict,
        account: Dict,
        positions: List[Dict],
        mark_price: float,
        kline_map: Dict[str, Dict[str, List[dict]]],
        pnl_daily: float,
    ) -> RiskResult:
        equity = self._equity(account)
        cut, reason = self._check_daily_cut(pnl_daily, equity)
        if cut:
            return RiskResult(0.0, 0.0, 0.0, f"blocked:{reason}", {"corr": 1.0, "vol": 1.0})

        atr = (features.get(symbol, {}).get("5m", {}) or {}).get("atr")
        caps = {"corr": 1.0, "vol": 1.0}
        if atr is None or atr <= 0 or mark_price <= 0:
            base_notional = max(RISK_MIN_NOTIONAL, equity * RISK_TARGET_PCT * 0.05)
        else:
            atr_ratio = atr / mark_price if mark_price > 0 else 0.0
            vol_scale = (1.0 / max(atr_ratio, 1e-4)) ** 0.5
            base_notional = equity * RISK_TARGET_PCT * vol_scale

        trend = (regime or {}).get("trend", "FLAT")
        side_up = side.upper().startswith("B")
        if trend == "UP" and side_up:
            base_notional *= 1.10
        if trend == "DOWN" and not side_up:
            base_notional *= 1.10
        if trend == "FLAT":
            base_notional *= 0.85

        corr_cap = self._calc_corr_cap(symbol, kline_map)
        caps["corr"] = corr_cap
        notional = base_notional * corr_cap
        notional = max(RISK_MIN_NOTIONAL, min(RISK_MAX_NOTIONAL, notional))

        qty = 0.0
        if mark_price > 0:
            qty = notional / mark_price
        r_unit = notional / max(1.0, equity)

        return RiskResult(
            qty=qty,
            notional=notional,
            r_unit=r_unit,
            reason=f"ok(reg={trend},corr_cap={corr_cap:.2f})",
            caps=caps,
        )
