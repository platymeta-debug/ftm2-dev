from __future__ import annotations

from typing import Dict
import math
import os


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except Exception:
        return default


W_TREND = _env_float("SC_W_TREND", 0.5)
W_MR = _env_float("SC_W_MR", 0.3)
W_BRK = _env_float("SC_W_BRK", 0.2)


def _load_thresholds() -> Dict[str, Dict[str, float]]:
    base = {
        "UP": {"enter": 0.35, "exit": -0.25},
        "DOWN": {"enter": 0.35, "exit": -0.25},
        "FLAT": {"enter": 0.55, "exit": -0.10},
    }
    for regime in base:
        ent = os.getenv(f"SC_{regime}_ENTER")
        ext = os.getenv(f"SC_{regime}_EXIT")
        if ent:
            try:
                base[regime]["enter"] = float(ent)
            except Exception:
                pass
        if ext:
            try:
                base[regime]["exit"] = float(ext)
            except Exception:
                pass
    return base


TH = _load_thresholds()


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(x: float, low: float, high: float) -> float:
    if x < low:
        return low
    if x > high:
        return high
    return x


class Forecaster:
    """Forecast ensemble that blends multi-timeframe features with regime context."""

    def __init__(self, features: Dict[str, Dict[str, dict]], regime_map: Dict[str, dict]):
        self.features = features
        self.regime_map = regime_map

    # [ANCHOR:SCORING]
    def _component_scores(self, f5: dict, f15: dict, f1h: dict, f4h: dict) -> Dict[str, float]:
        mom = 0.0
        if f5 and f15:
            mom = 0.6 * _clip(f15.get("ema_spread", 0.0), -0.01, 0.01)
            mom += 0.4 * _clip(f5.get("ema_slope", 0.0), -0.01, 0.01)
            adx = f15.get("adx", 0.0)
            if adx > 20.0:
                mom += 0.001 * (adx - 20.0)

        mr = 0.0
        if f5:
            mr = -_clip(f5.get("zscore", 0.0), -3.0, 3.0) * 0.15

        brk = 0.0
        if f15:
            brk = 0.5 * _clip(f15.get("donch", 0.0), -1.0, 1.0)
            brk += 0.5 * _clip(f15.get("bbw", 0.0), 0.0, 0.5)

        vol_pen = 0.0
        if f4h:
            rv = f4h.get("pr_rv20", f4h.get("rv_pr", 0.5))
            if rv > 0.85:
                vol_pen = -0.10
            elif rv > 0.75:
                vol_pen = -0.05

        return {"mom": mom, "meanrev": mr, "breakout": brk, "vol": vol_pen}

    def forecast_symbol(self, sym: str, horizon_k: int = 12) -> dict:
        feats = self.features.get(sym, {})
        f5 = feats.get("5m", {})
        f15 = feats.get("15m", {})
        f1h = feats.get("1h", {})
        f4h = feats.get("4h", {})

        if not f5 or not f15 or not f4h:
            return {"symbol": sym, "readiness": "BLOCKED", "reason": "insufficient_features"}

        regime = self.regime_map.get(sym, {"trend": "FLAT", "vol": "LOW"})
        comp = self._component_scores(f5, f15, f1h, f4h)
        base = W_TREND * comp["mom"] + W_MR * comp["meanrev"] + W_BRK * comp["breakout"] + comp["vol"]
        trend = regime.get("trend", "FLAT").upper()
        if trend == "UP":
            base += 0.10
        elif trend == "DOWN":
            base -= 0.10

        score = base
        p_up = _sigmoid(3.0 * score)

        th = TH.get(trend, TH["FLAT"])
        stance = "FLAT"
        if score >= th["enter"]:
            stance = "LONG"
        elif score <= -th["enter"]:
            stance = "SHORT"
        elif abs(score) < abs(th["exit"]):
            stance = "FLAT"

        readiness = "SCOUT"
        if abs(score) >= 0.25:
            readiness = "CANDIDATE"

        gates = {
            "regime_ok": True,
            "rv_band_ok": f4h.get("pr_rv20", f4h.get("rv_pr", 0.5)) <= 0.95,
            "risk_ok": True,
            "cooldown_ok": True,
            "cooldown_s": 0,
        }

        explain = {**comp, "regime": (0.10 if trend == "UP" else (-0.10 if trend == "DOWN" else 0.0))}

        return {
            "symbol": sym,
            "tf": "5m",
            "score": round(score, 4),
            "p_up": round(p_up, 4),
            "stance": stance,
            "readiness": readiness,
            "gates": gates,
            "horizon_k": horizon_k,
            "explain": explain,
            "plan": {
                "entry": "market",
                "size_qty_est": None,
                "notional_est": None,
                "risk_R": 0.1,
                "sl": 1.5,
                "tp_ladder": [1.0, 2.0, 3.0],
            },
        }


__all__ = ["Forecaster", "TH", "W_TREND", "W_MR", "W_BRK"]

