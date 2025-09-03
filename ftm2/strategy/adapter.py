# -*- coding: utf-8 -*-
"""
Strategy Adapter
- 실전 ForecastEnsemble ↔ 오프라인 러너(백테스트/리플레이) 연결 계층
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple, Protocol
import importlib, json, math, logging
from collections import deque, defaultdict

log = logging.getLogger("ftm2.strat")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class StrategyAdapterBase(Protocol):
    def infer(self, snapshot: Dict[str, Any], symbols: List[str], interval: str) -> Dict[str, Dict[str, Any]]:
        ...

# --- 더미(모멘텀 EMA) ---
class DummyMomentumAdapter:
    """
    입력: snapshot.features[(sym,interval)]에 c(종가), c_prev 가 있으면 log return 누적
    출력: {sym: {"stance": LONG/SHORT/FLAT, "score": [-1..1]}}
    """
    def __init__(self, lookback: int = 12, scale: float = 10.0) -> None:
        self.lookback = lookback
        self.scale = scale
        self._rets: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max(lookback*4, 32)))

    @staticmethod
    def _ema(prev: Optional[float], x: float, k: float) -> float:
        return (1-k)*prev + k*x if prev is not None else x

    def infer(self, snapshot: Dict[str, Any], symbols: List[str], interval: str) -> Dict[str, Dict[str, Any]]:
        feats = snapshot.get("features") or {}
        k = 2.0 / (self.lookback + 1)
        out: Dict[str, Dict[str, Any]] = {}
        for s in symbols:
            f = feats.get((s, interval), {})
            c = float(f.get("c") or 0.0)
            cp = float(f.get("c_prev") or 0.0)
            r = (math.log(c/cp) if c>0 and cp>0 else 0.0)
            self._rets[s].append(r)
            # EMA
            e = None
            for x in self._rets[s]:
                e = self._ema(e, x, k)
            score = max(-1.0, min(1.0, (e or 0.0) * self.scale))
            stance = "LONG" if score > 0 else "SHORT" if score < 0 else "FLAT"
            if abs(score) < 0.05:
                stance, score = "FLAT", 0.0
            out[s] = {"stance": stance, "score": score}
        return out

# --- 실전 ForecastEnsemble 어댑터 ---
class EnsembleAdapter:
    """
    ForecastEnsemble 래핑: ensemble.infer(snapshot) → {(sym,interval): {stance,score}}
    """
    def __init__(self, db=None, params: Optional[Dict[str, Any]] = None) -> None:
        self._ens = None
        self._err: Optional[str] = None
        try:
            try:
                from ftm2.forecast.ensemble import ForecastEnsemble  # type: ignore
            except Exception:
                from forecast.ensemble import ForecastEnsemble        # type: ignore
            # params가 있으면 생성자에 전달(미호환 시 예외 → 더미 폴백)
            self._ens = ForecastEnsemble(**(params or {}))
            log.info("[STRAT] ForecastEnsemble 활성")
        except Exception as e:
            self._err = str(e)
            log.warning("[STRAT][FALLBACK] ensemble 사용 불가 → dummy (%s)", e)
            self._ens = None
            self._dummy = DummyMomentumAdapter()

    def infer(self, snapshot: Dict[str, Any], symbols: List[str], interval: str) -> Dict[str, Dict[str, Any]]:
        if self._ens is None:
            return self._dummy.infer(snapshot, symbols, interval)
        res = self._ens.infer(snapshot)  # 기대: {(sym,interval): {...}}
        out: Dict[str, Dict[str, Any]] = {}
        for s in symbols:
            key = (s, interval)
            rec = res.get(key) or res.get(s) or {}
            stance = (rec.get("stance") or "FLAT").upper()
            score = float(rec.get("score") or 0.0)
            out[s] = {"stance": stance, "score": score}
        return out

# --- 커스텀 클래스 로딩 ---
def _import_string(path: str):
    mod, _, attr = path.rpartition(".")
    if not mod: raise ImportError(f"Invalid path: {path}")
    m = importlib.import_module(mod)
    return getattr(m, attr)

# --- 팩토리 ---
def create_adapter(mode: str = "dummy", class_path: Optional[str] = None, params: Optional[Dict[str, Any]] = None, db=None) -> StrategyAdapterBase:
    mode = (mode or "dummy").lower()
    if mode == "ensemble":
        return EnsembleAdapter(db=db, params=params)
    if mode == "custom" and class_path:
        try:
            Cls = _import_string(class_path)
            return Cls(**(params or {}))
        except Exception as e:
            log.warning("[STRAT][FALLBACK] custom 로드 실패 → dummy (%s)", e)
    # default
    return DummyMomentumAdapter()
