# -*- coding: utf-8 -*-
"""
Feature Pipeline (닫힌 봉 기반)
- 표준 라이브러리만 사용(NumPy/Pandas 의존 없음)
- 기본 피처:
  ret1, ret5, ret15
  ema_fast(12), ema_slow(26)
  atr14 (Wilder), rsi14 (Wilder)
  rv20 (수익률 표준편차), rng_atr((H-L)/ATR)
  pr_* (롤링 240개에 대한 퍼센타일 랭크; 0.0~1.0)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, List, Any, Optional
import math
import logging

log = logging.getLogger("ftm2.features")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ----------------------------- utils -----------------------------
class RollingSeries:
    """최대길이 고정 시계열(좌측 팝)"""
    __slots__ = ("_buf", "maxlen")
    def __init__(self, maxlen: int) -> None:
        self._buf: List[float] = []
        self.maxlen = int(maxlen)

    def append(self, x: float) -> None:
        self._buf.append(float(x))
        if len(self._buf) > self.maxlen:
            # 좌측 팝
            del self._buf[0]

    def last(self, n: int = 1) -> Optional[float]:
        if not self._buf or n <= 0 or n > len(self._buf):
            return None
        return self._buf[-n]

    def values(self) -> List[float]:
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)


def percentile_rank(sorted_vals: List[float], x: float) -> float:
    """정렬된 리스트에서 x의 분위수(0~1). 동률은 <= 기준."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    # 이진 탐색(간단 구현)
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    rank = lo  # <= x 개수
    return rank / n


@dataclass
class FeatureConfig:
    ema_fast: int = 12
    ema_slow: int = 26
    atr_n: int = 14
    rsi_n: int = 14
    rv_n: int = 20
    # 퍼센타일 캐시 길이
    pr_n: int = 240
    # 수익률 lookbacks
    ret_ns: Tuple[int, int, int] = (1, 5, 15)


class TAState:
    """심볼×인터벌별 누적 상태"""
    __slots__ = (
        "prev_c", "ema_f", "ema_s",
        "atr", "rsi_ag", "rsi_al",
        "closes", "rets", "trs"
    )
    def __init__(self, cfg: FeatureConfig) -> None:
        self.prev_c: Optional[float] = None
        self.ema_f: Optional[float] = None
        self.ema_s: Optional[float] = None
        self.atr: Optional[float] = None
        self.rsi_ag: Optional[float] = None  # avg gain
        self.rsi_al: Optional[float] = None  # avg loss
        self.closes = RollingSeries(maxlen=max(cfg.rv_n, 300))
        self.rets = RollingSeries(maxlen=300)
        self.trs = RollingSeries(maxlen=max(cfg.atr_n, 300))

    def update_bar(self, o: float, h: float, l: float, c: float, cfg: FeatureConfig) -> Dict[str, float]:
        """닫힌 봉 입력 → 상태 업데이트 & 기본 피처 일부 즉시 산출(EMA/ATR/RSI/ret1)"""
        # 수익률 & 히스토리
        if self.prev_c is not None and self.prev_c != 0.0:
            r1 = (c / self.prev_c) - 1.0
            self.rets.append(r1)
        else:
            r1 = 0.0
        self.closes.append(c)

        # EMA
        def _ema_step(prev: Optional[float], x: float, n: int) -> float:
            if prev is None:
                return x
            k = 2.0 / (n + 1.0)
            return (x - prev) * k + prev

        self.ema_f = _ema_step(self.ema_f, c, cfg.ema_fast)
        self.ema_s = _ema_step(self.ema_s, c, cfg.ema_slow)

        # TR/ATR (Wilder)
        if self.prev_c is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - self.prev_c), abs(l - self.prev_c))
        self.trs.append(tr)
        if self.atr is None:
            # 초기 시드: n개 평균(충분히 쌓이면 자연히 수렴)
            if len(self.trs) >= cfg.atr_n:
                self.atr = sum(self.trs.values()[-cfg.atr_n:]) / cfg.atr_n
        else:
            self.atr = (self.atr * (cfg.atr_n - 1) + tr) / cfg.atr_n

        # RSI (Wilder)
        if self.prev_c is not None:
            chg = c - self.prev_c
            gain = max(0.0, chg)
            loss = max(0.0, -chg)
            if self.rsi_ag is None or self.rsi_al is None:
                # 초기 시드: n개 단순평균 (충분히 쌓이면 자연히 수렴)
                if len(self.rets) >= cfg.rsi_n:
                    gains = [max(0.0, self.closes.values()[i] - self.closes.values()[i-1])
                             for i in range(1, len(self.closes))]
                    losses = [max(0.0, self.closes.values()[i-1] - self.closes.values()[i])
                              for i in range(1, len(self.closes))]
                    self.rsi_ag = (sum(gains[-cfg.rsi_n:]) / cfg.rsi_n) if gains else 0.0
                    self.rsi_al = (sum(losses[-cfg.rsi_n:]) / cfg.rsi_n) if losses else 0.0
            if self.rsi_ag is None: self.rsi_ag = 0.0
            if self.rsi_al is None: self.rsi_al = 0.0
            self.rsi_ag = (self.rsi_ag * (cfg.rsi_n - 1) + gain) / cfg.rsi_n
            self.rsi_al = (self.rsi_al * (cfg.rsi_n - 1) + loss) / cfg.rsi_n

        # prev close 업데이트
        self.prev_c = c

        # 즉시 산출 값들 반환(나머지는 엔진에서 추가 계산)
        out: Dict[str, float] = {
            "ret1": r1,
            "ema_fast": float(self.ema_f) if self.ema_f is not None else float(c),
            "ema_slow": float(self.ema_s) if self.ema_s is not None else float(c),
        }
        if self.atr is not None and self.atr > 0:
            out["atr14"] = float(self.atr)
            out["rng_atr"] = float((h - l) / self.atr)
        if self.rsi_ag is not None and self.rsi_al is not None:
            denom = self.rsi_al if self.rsi_al != 0.0 else 1e-12
            rs = self.rsi_ag / denom
            rsi = 100.0 - (100.0 / (1.0 + rs))
            out["rsi14"] = float(rsi)

        return out


# ----------------------------- engine -----------------------------
class FeatureEngine:
    """
    닫힌 봉이 들어올 때마다 피처를 계산한다.
    - process_snapshot(snapshot) 호출 시, 각 (심볼,인터벌) 최신 닫힌 봉을 감지하고 1회 계산
    """
    def __init__(self, symbols: List[str], intervals: List[str], cfg: FeatureConfig = FeatureConfig()) -> None:
        self.symbols = symbols
        self.intervals = intervals
        self.cfg = cfg
        self._state: Dict[Tuple[str, str], TAState] = {}
        self._last_T: Dict[Tuple[str, str], int] = {}
        # 퍼센타일 캐시(피처별)
        self._pr_cache: Dict[Tuple[str, str, str], RollingSeries] = {}  # (sym,itv,feat) -> series(maxlen=cfg.pr_n)

    def _state_of(self, sym: str, itv: str) -> TAState:
        k = (sym, itv)
        st = self._state.get(k)
        if st is None:
            st = TAState(self.cfg)
            self._state[k] = st
        return st

    def _pr_series(self, sym: str, itv: str, feat: str) -> RollingSeries:
        k = (sym, itv, feat)
        s = self._pr_cache.get(k)
        if s is None:
            s = RollingSeries(self.cfg.pr_n)
            self._pr_cache[k] = s
        return s

    def _std(self, xs: List[float]) -> float:
        n = len(xs)
        if n < 2:
            return 0.0
        m = sum(xs) / n
        var = sum((x - m) * (x - m) for x in xs) / n
        return math.sqrt(var)

    def _retN(self, rets: List[float], n: int) -> float:
        if len(rets) < n:
            return 0.0
        # 누적 로그 대신 단순 합산 근사: 작은 구간에서는 괜찮음
        s = 1.0
        for r in rets[-n:]:
            s *= (1.0 + r)
        return s - 1.0

    def process_snapshot(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        klines: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("klines", {})
        for sym in self.symbols:
            for itv in self.intervals:
                bar = klines.get((sym, itv))
                if not bar or not bar.get("x"):
                    continue  # 닫힌 봉만
                T = int(bar.get("T") or 0)
                key = (sym, itv)
                if self._last_T.get(key) == T:
                    continue  # 이미 처리
                self._last_T[key] = T

                o = float(bar.get("o", 0.0))
                h = float(bar.get("h", 0.0))
                l = float(bar.get("l", 0.0))
                c = float(bar.get("c", 0.0))

                st = self._state_of(sym, itv)
                feats = st.update_bar(o, h, l, c, self.cfg)

                # retN
                rets = st.rets.values()
                n1, n5, n15 = self.cfg.ret_ns
                feats["ret1"] = feats.get("ret1", 0.0)
                feats["ret5"] = self._retN(rets, n5)
                feats["ret15"] = self._retN(rets, n15)

                # rv20
                rv = self._std(rets[-self.cfg.rv_n:]) if len(rets) >= self.cfg.rv_n else self._std(rets)
                feats["rv20"] = float(rv)

                # 퍼센타일(롤링 pr_n)
                for key_name in ("ret1", "rv20", "atr14"):
                    if key_name in feats:
                        ser = self._pr_series(sym, itv, key_name)
                        ser.append(float(feats[key_name]))
                        sv = sorted(ser.values())
                        feats[f"pr_{key_name}"] = percentile_rank(sv, float(feats[key_name]))

                out.append({"symbol": sym, "interval": itv, "T": T, "features": feats})
                log.info("[FEATURES] %s %s T=%s ret1=%.5f rv20=%.5f atr=%.5f",
                         sym, itv, T, feats.get("ret1", 0.0), feats.get("rv20", 0.0), feats.get("atr14", 0.0))
        return out

    # [ANCHOR:FEATURES_UPDATE] begin
    def update(self, sym: str, itv: str, bus) -> None:
        snap = bus.snapshot()
        bar = snap.get("klines", {}).get((sym, itv))
        if not bar or not bar.get("x"):
            return
        st = self._state_of(sym, itv)
        o = float(bar.get("o", 0.0))
        h = float(bar.get("h", 0.0))
        l = float(bar.get("l", 0.0))
        c = float(bar.get("c", 0.0))
        feats = st.update_bar(o, h, l, c, self.cfg)
        rets = st.rets.values()
        rv = self._std(rets[-self.cfg.rv_n:]) if len(rets) >= self.cfg.rv_n else self._std(rets)
        ser = self._pr_series(sym, itv, "rv20")
        ser.append(float(rv))
        rv_pr = percentile_rank(sorted(ser.values()), float(rv))
        ret1 = feats.get("ret1", 0.0)
        atr = feats.get("atr14", 0.0)
        out = {
            "ret1": float(ret1),
            "rv20": float(rv),
            "atr": float(atr),
            "ema_fast": float(feats.get("ema_fast", c)),
            "ema_slow": float(feats.get("ema_slow", c)),
            "pr_rv20": float(rv_pr),
            "ts": int(bar.get("T") or 0),
        }
        bus.update_features(sym, itv, out)
        log.info("[FEATURES] %s %s T=%s ret1=%.5f rv20=%.5f atr=%.5f",
                 sym, itv, out["ts"], out["ret1"], out["rv20"], out["atr"])
    # [ANCHOR:FEATURES_UPDATE] end

