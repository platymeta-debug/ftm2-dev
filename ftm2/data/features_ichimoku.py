from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List
import os

from ftm2.data.streams import State


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except Exception:
        return default


IK_TENKAN = _env_int("IK_TENKAN", 9)
IK_KIJUN = _env_int("IK_KIJUN", 26)
IK_SEN = _env_int("IK_SEN", 52)
IK_TWIST_GUARD = _env_int("IK_TWIST_GUARD", 6)
IK_THICK_PCT = _env_float("IK_THICK_PCT", 0.90)


def _mid(hh: List[float], ll: List[float], window: int, idx: int) -> float:
    lo = min(ll[max(0, idx - window + 1) : idx + 1])
    hi = max(hh[max(0, idx - window + 1) : idx + 1])
    return (lo + hi) / 2.0


def _pct_rank_series(series: List[float], window: int) -> List[float]:
    out: List[float] = []
    window_buf: List[float] = []
    for value in series:
        window_buf.append(value)
        if len(window_buf) > window:
            window_buf.pop(0)
        if len(window_buf) < 2:
            out.append(0.5)
            continue
        count = sum(1 for w in window_buf if w <= value)
        out.append(count / len(window_buf))
    return out


@dataclass
class IchimokuEngine:
    """Compute Ichimoku feature maps for multiple timeframes."""

    state: State
    features: Dict[str, Dict[str, dict]] = field(default_factory=dict)

    # [ANCHOR:ICHIMOKU_FEATURES]
    def compute_tf(self, sym: str, tf: str) -> dict:
        dq = self.state.kline_map[sym][tf]
        nmin = max(IK_SEN, IK_KIJUN) + IK_KIJUN + 3
        if len(dq) < nmin:
            return {}

        highs = [bar["h"] for bar in dq]
        lows = [bar["l"] for bar in dq]
        closes = [bar["c"] for bar in dq]
        n = len(closes)

        tenkan: List[float] = []
        kijun: List[float] = []
        ssa_raw: List[float] = []
        ssb_raw: List[float] = []
        for i in range(n):
            ten = _mid(highs, lows, IK_TENKAN, i)
            kij = _mid(highs, lows, IK_KIJUN, i)
            tenkan.append(ten)
            kijun.append(kij)
            ssa_raw.append((ten + kij) / 2.0)
            ssb_raw.append(_mid(highs, lows, IK_SEN, i))

        def _slope(arr: List[float], window: int = 5) -> float:
            if len(arr) < window + 1:
                return 0.0
            return (arr[-1] - arr[-1 - window]) / max(1e-12, window)

        ssa = list(ssa_raw)
        ssb = list(ssb_raw)

        pos_vs_cloud = 0
        last_close = closes[-1]
        last_ssa = ssa[-1]
        last_ssb = ssb[-1]
        cloud_hi = max(last_ssa, last_ssb)
        cloud_lo = min(last_ssa, last_ssb)
        if last_close > cloud_hi:
            pos_vs_cloud = 1
        elif last_close < cloud_lo:
            pos_vs_cloud = -1

        cloud_thickness = abs(last_ssa - last_ssb) / max(1e-12, last_close)
        cloud_slope = {"ssa": _slope(ssa, 5), "ssb": _slope(ssb, 5)}

        tk_cross = 0
        if len(tenkan) >= 2 and len(kijun) >= 2:
            prev = 1 if tenkan[-2] > kijun[-2] else (-1 if tenkan[-2] < kijun[-2] else 0)
            cur = 1 if tenkan[-1] > kijun[-1] else (-1 if tenkan[-1] < kijun[-1] else 0)
            if cur != prev:
                tk_cross = 1 if cur == 1 else (-1 if cur == -1 else 0)

        tk_dist = abs(tenkan[-1] - kijun[-1]) / max(1e-12, last_close)

        def _kumo_break_recent(window: int = 3) -> int:
            signals: List[int] = []
            for i in range(max(0, n - window), n):
                hi = max(ssa[i], ssb[i])
                lo = min(ssa[i], ssb[i])
                if closes[i] > hi:
                    signals.append(1)
                elif closes[i] < lo:
                    signals.append(-1)
                else:
                    signals.append(0)
            for j in range(1, len(signals)):
                if signals[j] != signals[j - 1] and signals[j] != 0:
                    return signals[j]
            return 0

        kumo_break = _kumo_break_recent(3)

        chikou_conf = 0
        if n > IK_KIJUN:
            past_close = closes[-IK_KIJUN]
            if past_close < last_close:
                chikou_conf = 1
            elif past_close > last_close:
                chikou_conf = -1

        def _is_flat(arr: List[float], lookback: int = 5, tol: float = 1e-9) -> bool:
            if len(arr) < 2:
                return False
            base = arr[-1]
            for j in range(1, min(lookback, len(arr) - 1) + 1):
                if abs(arr[-1 - j] - base) > tol:
                    return False
            return True

        flat_kijun = _is_flat(kijun, 5)
        flat_ssb = _is_flat(ssb, 5)
        magnet_dist = min(
            abs(last_close - kijun[-1]),
            abs(last_close - ssb[-1]),
        ) / max(1e-12, last_close)

        def _twist_ahead(max_ahead: int = 30) -> int | None:
            diff_now = last_ssa - last_ssb
            if abs(diff_now) < 1e-12:
                return 0
            slope_a = cloud_slope["ssa"]
            slope_b = cloud_slope["ssb"]
            for step in range(1, max_ahead + 1):
                diff = diff_now + step * (slope_a - slope_b)
                if diff * diff_now <= 0:
                    return step
            return None

        twist_ahead = _twist_ahead(30)

        recent_thickness = [
            abs(ssa[i] - ssb[i]) / max(1e-12, closes[i])
            for i in range(max(0, n - IK_SEN), n)
        ]
        if recent_thickness:
            thick_pr = _pct_rank_series(recent_thickness, min(20, len(recent_thickness)))[-1]
        else:
            thick_pr = 0.5

        return {
            "tenkan": tenkan[-1],
            "kijun": kijun[-1],
            "ssa": last_ssa,
            "ssb": last_ssb,
            "chikou": closes[-IK_KIJUN] if n > IK_KIJUN else None,
            "pos_vs_cloud": pos_vs_cloud,
            "cloud_thickness": cloud_thickness,
            "cloud_thickness_pr": thick_pr,
            "cloud_slope": cloud_slope,
            "tk_cross": tk_cross,
            "tk_dist": tk_dist,
            "kumo_break": kumo_break,
            "chikou_conf": chikou_conf,
            "magnet": {
                "flat_kijun": flat_kijun,
                "flat_ssb": flat_ssb,
                "dist": magnet_dist,
            },
            "twist_ahead": twist_ahead,
        }

    def update(self, symbols: List[str]) -> Dict[str, Dict[str, dict]]:
        out: Dict[str, Dict[str, dict]] = {}
        for sym in symbols:
            sym_map: Dict[str, dict] = out.setdefault(sym, {})
            for tf in ("5m", "15m", "1h", "4h"):
                feat = self.compute_tf(sym, tf)
                if feat:
                    frame_map = sym_map.setdefault(tf, {})
                    frame_map["ichimoku"] = feat
        self.features = out
        return out


__all__ = ["IchimokuEngine"]
