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



def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_on(key: str, default: bool = True) -> bool:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    return str(raw).lower() in {"1", "true", "on", "yes"}


W_TREND = _env_float("SC_W_TREND", 0.5)
W_MR = _env_float("SC_W_MR", 0.3)
W_BRK = _env_float("SC_W_BRK", 0.2)
W_IMK = _env_float("W_IMK", 0.35)

IK_GATES = _env_on("IK_GATES", True)
IK_FAVOR_TREND = _env_on("IK_FAVOR_TREND", True)
IK_THICK_PCT = _env_float("IK_THICK_PCT", 0.90)
IK_TWIST_GUARD = _env_int("IK_TWIST_GUARD", 6)



def _load_thresholds() -> Dict[str, Dict[str, float]]:
    base = {
        "UP": {"enter": 0.35, "exit": -0.25},
        "DOWN": {"enter": 0.35, "exit": -0.25},
        "FLAT": {"enter": 0.55, "exit": -0.10},
    }
    for regime in base:

        enter_env = os.getenv(f"SC_{regime}_ENTER")
        exit_env = os.getenv(f"SC_{regime}_EXIT")
        if enter_env:
            try:
                base[regime]["enter"] = float(enter_env)
            except Exception:
                pass
        if exit_env:
            try:
                base[regime]["exit"] = float(exit_env)

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

    """Forecast ensemble that blends multi-timeframe features with regime and Ichimoku context."""


    def __init__(self, features: Dict[str, Dict[str, dict]], regime_map: Dict[str, dict]):
        self.features = features
        self.regime_map = regime_map

    # [ANCHOR:SCORING]

    def _component_scores_basic(self, f5: dict, f15: dict, f1h: dict, f4h: dict) -> Dict[str, float]:

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

            rv = f4h.get("rv_pr", f4h.get("pr_rv20", 0.5))

            if rv > 0.85:
                vol_pen = -0.10
            elif rv > 0.75:
                vol_pen = -0.05

        return {"mom": mom, "meanrev": mr, "breakout": brk, "vol": vol_pen}


    # [ANCHOR:IMK_COMPONENT]
    def _component_ichimoku(
        self, f5: dict, f15: dict, f1h: dict, f4h: dict, regime_trend: str
    ) -> Dict[str, float | Dict[str, float]]:
        def _imk(feat: dict) -> dict:
            return (feat or {}).get("ichimoku", {})

        i5 = _imk(f5)
        i15 = _imk(f15)
        i1h = _imk(f1h)
        i4h = _imk(f4h)
        if not (i5 or i15 or i4h):
            return {
                "imk": 0.0,
                "imk_parts": {"tk": 0.0, "pos": 0.0, "kumo": 0.0, "chikou": 0.0, "slope": 0.0, "magnet": 0.0},
            }

        def _score_cross(info: dict) -> float:
            sgn = info.get("tk_cross", 0)
            if sgn == 1:
                return 1.0
            if sgn == -1:
                return -1.0
            return 0.0

        tk = 0.0
        if i5:
            tk += 0.6 * _score_cross(i5)
        if i15:
            tk += 0.4 * _score_cross(i15)

        def _pos(info: dict) -> float:
            return float(info.get("pos_vs_cloud", 0))

        pos = 0.0
        if i15:
            pos += 0.6 * _pos(i15)
        if i1h:
            pos += 0.4 * _pos(i1h)

        def _break(info: dict) -> float:
            sgn = info.get("kumo_break", 0)
            if sgn == 1:
                return 1.0
            if sgn == -1:
                return -1.0
            return 0.0

        kumo = 0.0
        if i15:
            kumo += 0.6 * _break(i15)
        if i5:
            kumo += 0.4 * _break(i5)

        def _chik(info: dict) -> float:
            sgn = info.get("chikou_conf", 0)
            if sgn == 1:
                return 1.0
            if sgn == -1:
                return -1.0
            return 0.0

        chik = 0.0
        if i15:
            chik += 0.6 * _chik(i15)
        if i1h:
            chik += 0.4 * _chik(i1h)

        slope = 0.0
        if i4h:
            slope = _clip(i4h.get("cloud_slope", {}).get("ssa", 0.0), -0.01, 0.01) * 8.0

        magnet = 0.0
        for info, weight in ((i5, 0.4), (i15, 0.6)):
            if not info:
                continue
            mag = info.get("magnet", {})
            dist = float(mag.get("dist", 0.0))
            flat = bool(mag.get("flat_kijun") or mag.get("flat_ssb"))
            if flat and dist < 0.002:
                magnet -= 0.2 * weight

        imk_score = 0.35 * tk + 0.25 * pos + 0.20 * kumo + 0.10 * chik + 0.10 * slope + 0.00 * magnet

        if IK_FAVOR_TREND and i4h:
            pos4 = float(i4h.get("pos_vs_cloud", 0))
            if regime_trend == "UP":
                imk_score += 0.10 * pos4
            elif regime_trend == "DOWN":
                imk_score -= 0.10 * pos4

        return {
            "imk": imk_score,
            "imk_parts": {"tk": tk, "pos": pos, "kumo": kumo, "chikou": chik, "slope": slope, "magnet": magnet},
        }


    def forecast_symbol(self, sym: str, horizon_k: int = 12) -> dict:
        feats = self.features.get(sym, {})
        f5 = feats.get("5m", {})
        f15 = feats.get("15m", {})
        f1h = feats.get("1h", {})
        f4h = feats.get("4h", {})

        if not f5 or not f15 or not f4h:
            return {"symbol": sym, "readiness": "BLOCKED", "reason": "insufficient_features"}

        regime = self.regime_map.get(sym, {"trend": "FLAT", "vol": "LOW"})

        basic = self._component_scores_basic(f5, f15, f1h, f4h)
        ichimoku = self._component_ichimoku(f5, f15, f1h, f4h, regime.get("trend", "FLAT"))

        score = (
            W_TREND * basic["mom"]
            + W_MR * basic["meanrev"]
            + W_BRK * basic["breakout"]
            + W_IMK * ichimoku["imk"]
            + basic["vol"]
        )

        trend = regime.get("trend", "FLAT").upper()
        if trend == "UP":
            score += 0.10
        elif trend == "DOWN":
            score -= 0.10


        p_up = _sigmoid(3.0 * score)

        th = TH.get(trend, TH["FLAT"])
        stance = "FLAT"
        enter_th = th["enter"]
        exit_th = th["exit"]
        if score >= enter_th:
            stance = "LONG"
        elif score <= -enter_th:
            stance = "SHORT"
        elif abs(score) < abs(exit_th):
            stance = "FLAT"

        gates = {
            "regime_ok": True,
            "rv_band_ok": f4h.get("rv_pr", f4h.get("pr_rv20", 0.5)) <= 0.95,
            "risk_ok": True,
            "cooldown_ok": True,
            "cloud_consistency": True,
            "cloud_thick_ok": True,
            "twist_guard_ok": True,
            "cooldown_s": 0,
        }

        i4h = (f4h or {}).get("ichimoku", {})
        i1h = (f1h or {}).get("ichimoku", {})
        if IK_GATES:
            pos4 = i4h.get("pos_vs_cloud") if isinstance(i4h, dict) else None
            pos1 = i1h.get("pos_vs_cloud") if isinstance(i1h, dict) else None
            if pos4 is not None and pos1 is not None:
                if pos4 == 1 and pos1 == 1 and stance == "SHORT":
                    gates["cloud_consistency"] = False
                if pos4 == -1 and pos1 == -1 and stance == "LONG":
                    gates["cloud_consistency"] = False

            thick_pr = i4h.get("cloud_thickness_pr") if isinstance(i4h, dict) else None
            if isinstance(thick_pr, (int, float)) and thick_pr >= IK_THICK_PCT:
                gates["cloud_thick_ok"] = False

            ta = i4h.get("twist_ahead") if isinstance(i4h, dict) else None
            if isinstance(ta, int) and ta <= IK_TWIST_GUARD:
                gates["twist_guard_ok"] = False
            if ta == 0:
                gates["twist_guard_ok"] = False

        readiness = "SCOUT"
        if abs(score) >= 0.25:
            readiness = "CANDIDATE"
        if IK_GATES and not all(
            gates[key]
            for key in ("cloud_consistency", "cloud_thick_ok", "twist_guard_ok")
        ):
            readiness = "SCOUT"

        explain = {
            "mom": round(basic["mom"], 4),
            "meanrev": round(basic["meanrev"], 4),
            "breakout": round(basic["breakout"], 4),
            "vol": round(basic["vol"], 4),
            "regime": 0.10 if trend == "UP" else (-0.10 if trend == "DOWN" else 0.0),
            "imk": round(float(ichimoku["imk"]), 4),
            "imk_parts": {k: round(v, 4) for k, v in ichimoku["imk_parts"].items()},
        }


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


__all__ = [
    "Forecaster",
    "TH",
    "W_TREND",
    "W_MR",
    "W_BRK",
    "W_IMK",
]


