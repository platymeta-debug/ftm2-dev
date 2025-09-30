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
import os

log = logging.getLogger("ftm2.features")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# [ANCHOR:FEATURES_LOG] begin
_feat_count: Dict[Tuple[str, str], int] = {}


def _is_backfill(ret1, rv20, atr, idx, total):
    if total and total >= 200:
        return True
    if (rv20 is None or atr is None):
        return True
    return (abs(rv20) < 1e-12 and abs(atr) < 1e-12 and idx < 5)


def log_features(logger, symbol: str, tf: str, i: int, total: int, T: int, ret1: float, rv20: float, atr: float) -> None:
    mode = os.getenv("FEATURES_LOG_MODE", "sample").lower()
    if mode == "off":
        if i == total - 1:
            logger.info(f"[FEATURES][SUMMARY] {symbol} {tf} warmup {total} bars (log=off)")
        return
    step = max(1, int(os.getenv("FEATURES_LOG_SAMPLE_N", "80")))
    backfill = _is_backfill(ret1, rv20, atr, i, total)
    if backfill:
        if i in (0, total - 1) or (i % step == 0):
            logger.info(
                f"[FEATURES] {symbol} {tf} T={T} ret1={ret1:+.5f} rv20={rv20:.5f} atr={atr:.5f} (warmup)"
            )
        elif i == total - 1:
            logger.info(
                f"[FEATURES][SUMMARY] {symbol} {tf} warmup {total} bars (sampled)"
            )
    else:
        if i == total - 1 or (i % step == 0):
            logger.info(
                f"[FEATURES] {symbol} {tf} T={T} ret1={ret1:+.5f} rv20={rv20:.5f} atr={atr:.5f}"
            )
# [ANCHOR:FEATURES_LOG] end


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


def _wilder_smooth(vals: List[float], n: int) -> List[float]:
    if n <= 0 or len(vals) < n:
        return []
    window = vals[:n]
    avg = sum(window) / n
    out: List[float] = [avg]
    for v in vals[n:]:
        avg = ((avg * (n - 1)) + v) / n
        out.append(avg)
    return out


def _calc_adx(highs: List[float], lows: List[float], closes: List[float], n: int) -> float:
    if n <= 0 or len(closes) <= n:
        return 0.0
    dm_p: List[float] = []
    dm_m: List[float] = []
    tr: List[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        dm_p.append(up if up > dn and up > 0 else 0.0)
        dm_m.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    tr_s = _wilder_smooth(tr, n)
    dm_p_s = _wilder_smooth(dm_p, n)
    dm_m_s = _wilder_smooth(dm_m, n)
    if not tr_s or not dm_p_s or not dm_m_s:
        return 0.0
    di_p: List[float] = []
    di_m: List[float] = []
    for i in range(len(tr_s)):
        denom = tr_s[i]
        if denom == 0:
            di_p.append(0.0)
            di_m.append(0.0)
        else:
            di_p.append(100.0 * dm_p_s[i] / denom)
            di_m.append(100.0 * dm_m_s[i] / denom)
    dx: List[float] = []
    for i in range(len(di_p)):
        denom = di_p[i] + di_m[i]
        if denom == 0:
            dx.append(0.0)
        else:
            dx.append(100.0 * abs(di_p[i] - di_m[i]) / denom)
    adx = _wilder_smooth(dx, n)
    return adx[-1] if adx else 0.0


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except Exception:
        return default


@dataclass
class FeatureConfig:
    ema_fast: int = _env_int("FE_EMA_FAST", 20)
    ema_slow: int = _env_int("FE_EMA_SLOW", 50)
    ema_long: int = _env_int("FE_EMA_LONG", 200)
    atr_n: int = _env_int("FE_WIN_ATR", 14)
    rsi_n: int = 14
    rv_n: int = _env_int("FE_WIN_RV", 20)
    bb_n: int = _env_int("FE_BB_WIN", 20)
    donch_n: int = _env_int("FE_DON_WIN", 20)
    adx_n: int = 14
    slope_k: int = 5
    # 퍼센타일 캐시 길이
    pr_n: int = 240
    # 수익률 lookbacks
    ret_ns: Tuple[int, int, int] = (1, 5, 15)


class TAState:
    """심볼×인터벌별 누적 상태"""
    __slots__ = (
        "prev_c",
        "prev_h",
        "prev_l",
        "ema_f",
        "ema_s",
        "ema_l",
        "ema_f_hist",
        "ema_s_hist",
        "ema_l_hist",
        "atr",
        "rsi_ag",
        "rsi_al",
        "closes",
        "rets",
        "trs",
        "highs",
        "lows",
        "dm_p",
        "dm_m",
        "tr_raw",
    )
    def __init__(self, cfg: FeatureConfig) -> None:
        self.prev_c: Optional[float] = None
        self.prev_h: Optional[float] = None
        self.prev_l: Optional[float] = None
        self.ema_f: Optional[float] = None
        self.ema_s: Optional[float] = None
        self.ema_l: Optional[float] = None
        self.ema_f_hist = RollingSeries(maxlen=400)
        self.ema_s_hist = RollingSeries(maxlen=400)
        self.ema_l_hist = RollingSeries(maxlen=800)
        self.atr: Optional[float] = None
        self.rsi_ag: Optional[float] = None  # avg gain
        self.rsi_al: Optional[float] = None  # avg loss
        self.closes = RollingSeries(maxlen=max(cfg.rv_n, cfg.bb_n, cfg.donch_n, 400))
        self.rets = RollingSeries(maxlen=400)
        self.trs = RollingSeries(maxlen=max(cfg.atr_n, 400))
        self.highs = RollingSeries(maxlen=max(cfg.donch_n, 400))
        self.lows = RollingSeries(maxlen=max(cfg.donch_n, 400))
        self.dm_p = RollingSeries(maxlen=max(cfg.adx_n * 2, 400))
        self.dm_m = RollingSeries(maxlen=max(cfg.adx_n * 2, 400))
        self.tr_raw = RollingSeries(maxlen=max(cfg.adx_n * 2, 400))

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
        self.ema_l = _ema_step(self.ema_l, c, cfg.ema_long)
        if self.ema_f is not None:
            self.ema_f_hist.append(float(self.ema_f))
        if self.ema_s is not None:
            self.ema_s_hist.append(float(self.ema_s))
        if self.ema_l is not None:
            self.ema_l_hist.append(float(self.ema_l))

        # TR/ATR (Wilder)
        if self.prev_c is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - self.prev_c), abs(l - self.prev_c))
        self.trs.append(tr)
        self.tr_raw.append(tr)
        if self.atr is None:
            # 초기 시드: n개 평균(충분히 쌓이면 자연히 수렴)
            if len(self.trs) >= cfg.atr_n:
                self.atr = sum(self.trs.values()[-cfg.atr_n:]) / cfg.atr_n
        else:
            self.atr = (self.atr * (cfg.atr_n - 1) + tr) / cfg.atr_n

        # 히스토리 유지
        self.highs.append(h)
        self.lows.append(l)
        if self.prev_h is not None and self.prev_l is not None:
            up = h - self.prev_h
            dn = self.prev_l - l
            dm_p = up if up > dn and up > 0 else 0.0
            dm_m = dn if dn > up and dn > 0 else 0.0
        else:
            dm_p = dm_m = 0.0
        self.dm_p.append(dm_p)
        self.dm_m.append(dm_m)

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
        self.prev_h = h
        self.prev_l = l

        # 즉시 산출 값들 반환(나머지는 엔진에서 추가 계산)
        out: Dict[str, float] = {
            "ret1": r1,
            "ema_fast": float(self.ema_f) if self.ema_f is not None else float(c),
            "ema_slow": float(self.ema_s) if self.ema_s is not None else float(c),
        }
        if self.ema_l is not None:
            out["ema_long"] = float(self.ema_l)
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
                key = (sym, itv)
                i = _feat_count.get(key, 0)
                _feat_count[key] = i + 1
                log_features(
                    log,
                    sym,
                    itv,
                    i,
                    10 ** 9,
                    T,
                    feats.get("ret1", 0.0),
                    feats.get("rv20", 0.0),
                    feats.get("atr14", 0.0),
                )
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
        ema_fast = float(feats.get("ema_fast", c))
        ema_slow = float(feats.get("ema_slow", c))
        ema_long = float(feats.get("ema_long", ema_slow))

        ema_hist = st.ema_f_hist.values()
        slope_k = max(1, self.cfg.slope_k)
        if len(ema_hist) > slope_k:
            ema_slope = (ema_hist[-1] - ema_hist[-1 - slope_k]) / slope_k
        else:
            ema_slope = 0.0
        ema_spread = 0.0 if ema_slow == 0.0 else (ema_fast - ema_slow) / ema_slow
        long_spread = 0.0 if ema_long == 0.0 else (ema_slow - ema_long) / ema_long

        closes = st.closes.values()
        window = closes[-self.cfg.bb_n :] if len(closes) >= 2 else closes
        if len(window) >= 2:
            mu = sum(window) / len(window)
            std = self._std(window)
            zscore = 0.0 if std == 0.0 else (c - mu) / std
            bbw = 0.0 if mu == 0.0 else (2.0 * std) / mu
        else:
            zscore = 0.0
            bbw = 0.0

        highs = st.highs.values()
        lows = st.lows.values()
        if highs and lows:
            hi = max(highs[-self.cfg.donch_n :])
            lo = min(lows[-self.cfg.donch_n :])
            if hi != lo:
                donch = ((h - lo) / (hi - lo)) - ((hi - l) / (hi - lo))
            else:
                donch = 0.0
        else:
            donch = 0.0

        adx = 0.0
        if highs and lows and closes:
            min_len = min(len(highs), len(lows), len(closes))
            if min_len > self.cfg.adx_n:
                h_seq = highs[-min_len:]
                l_seq = lows[-min_len:]
                c_seq = closes[-min_len:]
                adx = _calc_adx(h_seq, l_seq, c_seq, self.cfg.adx_n)

        ts_val = int(bar.get("T") or 0)

        def _safe(v: float) -> float:
            try:
                v_f = float(v)
            except Exception:
                v_f = 0.0
            return v_f if math.isfinite(v_f) else 0.0

        out = {
            "ret1": _safe(ret1),
            "rv20": _safe(rv),
            "atr": _safe(atr),
            "ema_fast": _safe(ema_fast),
            "ema_slow": _safe(ema_slow),
            "ema_long": _safe(ema_long),
            "ema_slope": _safe(ema_slope),
            "ema_spread": _safe(ema_spread),
            "long_spread": _safe(long_spread),
            "pr_rv20": _safe(rv_pr),
            "zscore": _safe(zscore),
            "bbw": _safe(bbw),
            "donch": _safe(donch),
            "adx": _safe(adx),
            "price": _safe(c),
            "ts": ts_val,
        }
        bus.update_features(sym, itv, out)
        key = (sym, itv)
        i = _feat_count.get(key, 0)
        _feat_count[key] = i + 1
        log_features(
            log,
            sym,
            itv,
            i,
            10 ** 9,
            out["ts"],
            out["ret1"],
            out["rv20"],
            out["atr"],
        )
    # [ANCHOR:FEATURES_UPDATE] end

