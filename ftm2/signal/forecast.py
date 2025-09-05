# -*- coding: utf-8 -*-
"""
Forecast Ensemble & Scoring
- 기초 모델 3종: Trend(EMA 스프레드), MR(RSI 중심), Cross(ret1 vs RV & 범위/ATR)
- 레짐별 가중치 테이블 + 온라인 성과(지수가중 정확도)로 미세 조정
- 출력: score(-1..+1), prob_up(0..1), stance(LONG/SHORT/FLAT), components, weights, regime
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple, List, Any
import math
import logging

log = logging.getLogger("ftm2.forecast")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ----------------------------- 설정 -----------------------------
@dataclass
class ForecastConfig:
    # 스케일/임계
    spread_scale: float = 0.0010     # EMA 스프레드 정규화 스케일
    mr_center: float = 50.0          # RSI 중심
    mr_scale: float = 25.0           # RSI 정규화 스케일(≈14 기간)
    strong_thr: float = 0.60         # 강신호 임계(|score| 이상)
    flat_thr: float = 0.15           # 미약 구간(|score| 미만 → FLAT)

    # 온라인 가중치 조정
    lambda_perf: float = 0.02        # 성과 지수평균 학습률 (0.01~0.05 권장)
    w_clip_lo: float = 0.10          # 각 컴포넌트 가중치 하한
    w_clip_hi: float = 0.80          # 각 컴포넌트 가중치 상한

    # 기본 레짐별 가중치 (trend, mr, cross)
    base_weights: Dict[str, Tuple[float, float, float]] = field(default_factory=lambda: {
        "TREND_UP":   (0.60, 0.10, 0.30),
        "TREND_DOWN": (0.60, 0.10, 0.30),
        "RANGE_HIGH": (0.20, 0.30, 0.50),
        "RANGE_LOW":  (0.20, 0.60, 0.20),
    })


# ----------------------------- 내부 유틸 -----------------------------
def _clip(x: float, lo: float, hi: float) -> float:
    return hi if x > hi else lo if x < lo else x


def _tanh(x: float) -> float:
    try:
        return math.tanh(x)
    except Exception:  # pragma: no cover
        return 1.0 if x > 0 else -1.0 if x < 0 else 0.0


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:  # pragma: no cover
        return 0.0 if x < 0 else 1.0


# ----------------------------- 본체 -----------------------------
# [ANCHOR:FORECAST]
class ForecastEnsemble:
    """
    features[(sym,itv)], regimes[(sym,itv)] 를 사용한다.
    - 새 닫힌 봉의 ret1을 이용해 직전 예측의 성과를 갱신(온라인 성능 추적)
    """
    def __init__(self, symbols: List[str], interval: str, cfg: ForecastConfig = ForecastConfig()) -> None:
        self.symbols = symbols
        self.interval = interval
        self.cfg = cfg

        # 마지막 처리된 T / 직전 예측 캐시(성과 확인용)
        self._last_T: Dict[str, int] = {}
        self._pending_pred: Dict[str, Dict[str, Any]] = {}

        # 레짐별 성과(지수가중 정확도) — 0..1
        # perf[(regime, comp)] = ew_acc
        self._perf: Dict[Tuple[str, str], float] = {}

    # -------------------- 컴포넌트 스코어 --------------------
    # [ANCHOR:SCORING]
    def _score_trend(self, feats: Dict[str, Any]) -> float:
        ema_f = float(feats.get("ema_fast", 0.0))
        ema_s = float(feats.get("ema_slow", 1e-12)) or 1e-12
        spread = (ema_f - ema_s) / (ema_s if ema_s != 0.0 else 1e-12)
        s = _tanh(spread / (self.cfg.spread_scale if self.cfg.spread_scale != 0.0 else 1e-12))
        return _clip(s, -1.0, 1.0)

    def _score_mr(self, feats: Dict[str, Any]) -> float:
        rsi = float(feats.get("rsi14", 50.0))
        s = (self.cfg.mr_center - rsi) / self.cfg.mr_scale
        return _clip(_tanh(s), -1.0, 1.0)

    def _score_cross(self, feats: Dict[str, Any]) -> float:
        ret1 = float(feats.get("ret1", 0.0))
        rv = float(feats.get("rv20", 0.0))
        rng_atr = float(feats.get("rng_atr", 1.0))
        mag = abs(ret1) / (rv if rv > 1e-12 else 1e-12)
        mag = _clip(mag, 0.0, 3.0) / 3.0
        s = (1.0 if ret1 >= 0 else -1.0) * mag * _clip(rng_atr, 0.0, 2.0) / 2.0
        return _clip(s, -1.0, 1.0)

    # -------------------- 가중치/성과 --------------------
    def _weights_for(self, regime: str) -> Dict[str, float]:
        base = self.cfg.base_weights.get(regime) or self.cfg.base_weights["RANGE_LOW"]
        w = {"trend": base[0], "mr": base[1], "cross": base[2]}

        # 성과 기반 미세 조정
        adj: Dict[str, float] = {}
        for comp in ("trend", "mr", "cross"):
            ew = self._perf.get((regime, comp), 0.5)
            mult = 0.5 + ew
            adj[comp] = w[comp] * mult

        s = sum(adj.values()) or 1.0
        for k in adj:
            v = adj[k] / s
            adj[k] = _clip(v, self.cfg.w_clip_lo, self.cfg.w_clip_hi)
        s2 = sum(adj.values())
        for k in adj:
            adj[k] = adj[k] / (s2 if s2 != 0.0 else 1.0)
        return adj

    def _update_perf(self, sym: str, feats_now: Dict[str, Any]) -> None:
        pp = self._pending_pred.get(sym)
        if not pp:
            return
        ret1_now = float(feats_now.get("ret1", 0.0))
        if ret1_now == 0.0:
            return
        sign_ret = 1.0 if ret1_now > 0 else -1.0
        regime = pp.get("regime") or "RANGE_LOW"
        lr = self.cfg.lambda_perf
        for comp, sign_pred in pp.get("signs", {}).items():
            key = (regime, comp)
            prev = self._perf.get(key, 0.5)
            acc = 1.0 if (sign_pred * sign_ret) > 0 else 0.0
            self._perf[key] = (1 - lr) * prev + lr * acc
        self._pending_pred.pop(sym, None)

    # -------------------- 메인 --------------------
    def process_snapshot(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        feats_map: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("features", {})
        regimes: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("regimes", {})
        klines: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("klines", {})

        rows: List[Dict[str, Any]] = []
        for sym in self.symbols:
            key = (sym, self.interval)
            feats = feats_map.get(key)
            bar = klines.get(key)
            if not feats or not bar or not bar.get("x"):
                continue
            T = int(bar.get("T") or 0)
            if self._last_T.get(sym) == T:
                continue

            # 1) 직전 예측 성과 갱신
            self._update_perf(sym, feats)

            # 2) 컴포넌트 계산
            comp_scores = {
                "trend": self._score_trend(feats),
                "mr": self._score_mr(feats),
                "cross": self._score_cross(feats),
            }
            comp_signs = {k: (1.0 if v >= 0 else -1.0) for k, v in comp_scores.items()}

            # 3) 레짐 가중치
            regime_code = (regimes.get(key) or {}).get("code") or "RANGE_LOW"
            w = self._weights_for(regime_code)

            # 4) 앙상블 스코어/확률/스탠스
            score = sum(comp_scores[c] * w[c] for c in ("trend", "mr", "cross"))
            score = _clip(score, -1.0, 1.0)
            prob_up = _sigmoid(2.0 * score)
            if abs(score) >= self.cfg.strong_thr:
                stance = "LONG" if score > 0 else "SHORT"
            elif abs(score) >= self.cfg.flat_thr:
                stance = "LONG" if score > 0 else "SHORT"
            else:
                stance = "FLAT"

            explain = {
                "mom": comp_scores["trend"] * w["trend"],
                "meanrev": comp_scores["mr"] * w["mr"],
                "breakout": comp_scores["cross"] * w["cross"],
                "vol": 0.0,
                "regime": 0.0,
            }
            fc = {
                "score": float(score),
                "prob_up": float(prob_up),
                "p_up": float(prob_up),
                "stance": stance,
                "components": comp_scores,
                "weights": w,
                "regime": regime_code,
                "explain": explain,
            }
            rows.append({"symbol": sym, "interval": self.interval, "T": T, "forecast": fc})

            # 5) 온라인 성과 평가용 pending 저장
            self._pending_pred[sym] = {"signs": comp_signs, "regime": regime_code}
            self._last_T[sym] = T

            log.info(
                "[FORECAST] %s/%s score=%.3f p_up=%.3f stance=%s r=%s",
                sym,
                self.interval,
                fc["score"],
                fc["prob_up"],
                fc["stance"],
                regime_code,
            )

        return rows
