from __future__ import annotations

from typing import Dict, List
import os
import time


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    return str(raw).lower() in {"1", "true", "on", "yes"}


IK_GATES = _env_bool("IK_GATES", True)
IK_TWIST_GUARD = int(os.getenv("IK_TWIST_GUARD", "6"))
IK_THICK_PCT = float(os.getenv("IK_THICK_PCT", "0.90"))
REGIME_ALIGN_MODE = os.getenv("REGIME_ALIGN_MODE", "soft")
REENTER_COOLDOWN_S = int(os.getenv("REENTER_COOLDOWN_S", "60"))


class GateKeeper:
    def __init__(self) -> None:
        self._last_exit: Dict[str, float] = {}

    def mark_exit(self, symbol: str) -> None:
        self._last_exit[symbol] = time.time()

    def evaluate(self, ctx: Dict) -> Dict:
        symbol = ctx["symbol"]
        forecast = ctx.get("forecast", {})
        features = ctx.get("features", {})
        feats_1h = features.get(symbol, {}).get("1h", {})
        feats_4h = features.get(symbol, {}).get("4h", {})
        regime = ctx.get("regime", {"trend": "FLAT"})
        stance = forecast.get("stance", "FLAT")

        blocked: List[str] = []
        allow = True

        last_exit = self._last_exit.get(symbol, 0.0)
        cooldown_left = 0
        if last_exit:
            elapsed = time.time() - last_exit
            if elapsed < REENTER_COOLDOWN_S:
                blocked.append("cooldown")
                allow = False
                cooldown_left = max(0, REENTER_COOLDOWN_S - int(elapsed))
            else:
                cooldown_left = 0
        else:
            cooldown_left = 0

        trend = regime.get("trend")
        if REGIME_ALIGN_MODE == "strict":
            if trend == "UP" and stance == "SHORT":
                blocked.append("regime")
                allow = False
            if trend == "DOWN" and stance == "LONG":
                blocked.append("regime")
                allow = False

        if IK_GATES and feats_4h:
            ich4 = feats_4h.get("ichimoku", {}) or {}
            ich1 = feats_1h.get("ichimoku", {}) or {}

            if ich4 and ich1:
                if ich4.get("pos_vs_cloud") == 1 and ich1.get("pos_vs_cloud") == 1 and stance == "SHORT":
                    blocked.append("cloud_consistency")
                    allow = False
                if ich4.get("pos_vs_cloud") == -1 and ich1.get("pos_vs_cloud") == -1 and stance == "LONG":
                    blocked.append("cloud_consistency")
                    allow = False

            thick_pr = ich4.get("cloud_thickness_pr", 0.5)
            if thick_pr >= IK_THICK_PCT and (forecast.get("explain", {}) or {}).get("breakout", 0.0) > 0:
                blocked.append("cloud_thick")
                allow = False

            twist_ahead = ich4.get("twist_ahead")
            if twist_ahead is not None and twist_ahead <= IK_TWIST_GUARD:
                blocked.append("twist_guard")
                allow = False

        positions: List[Dict] = ctx.get("positions", [])
        long_qty = sum(p.get("qty", 0.0) for p in positions if p.get("symbol") == symbol and p.get("qty", 0.0) > 0)
        short_qty = sum(-p.get("qty", 0.0) for p in positions if p.get("symbol") == symbol and p.get("qty", 0.0) < 0)
        if long_qty > 0 and stance == "SHORT":
            blocked.append("position_conflict")
            allow = False
        if short_qty > 0 and stance == "LONG":
            blocked.append("position_conflict")
            allow = False

        return {"allow": allow, "blocked": blocked, "cooldown_s": cooldown_left}
