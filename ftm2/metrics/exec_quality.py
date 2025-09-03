# -*- coding: utf-8 -*-
"""
Execution Quality Reporter
- 롤링 윈도우 기반 슬리피지(bps)·넛지/취소 카운트 집계
- 표준 라이브러리만 사용
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple
from collections import deque, defaultdict
import time, bisect, statistics, math
import logging

log = logging.getLogger("ftm2.execq")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@dataclass
class ExecQConfig:
    window_sec: int = 600          # 롤링 윈도우 (기본 10분)
    alert_p90_bps: float = 8.0     # p90 bps 임계 초과 시 알림
    min_fills: int = 5             # 유의 통계 최소 체결 수
    report_sec: int = 30           # 리포트 주기 (초)

# 내부 기록 단위
class _Ring:
    __slots__ = ("buf", "window_ms")
    def __init__(self, window_ms: int) -> None:
        self.buf: deque[Tuple[int, float]] = deque()  # (ts_ms, slip_bps)
        self.window_ms = window_ms

    def push(self, ts_ms: int, bps: float) -> None:
        self.buf.append((ts_ms, bps))
        self._gc(ts_ms)

    def _gc(self, now_ms: int) -> None:
        cut = now_ms - self.window_ms
        while self.buf and self.buf[0][0] < cut:
            self.buf.popleft()

    def values(self, now_ms: int) -> List[float]:
        self._gc(now_ms)
        return [v for _, v in self.buf]

# [ANCHOR:EXEC_QUALITY]
class ExecQualityReporter:
    """
    - ingest_fill() 로 체결 유입
    - ingest_nudges()/ingest_cancels() 로 보조 카운트 유입
    - summary() 로 전체/심볼별 통계 반환
    """
    def __init__(self, cfg: ExecQConfig = ExecQConfig()) -> None:
        self.cfg = cfg
        self._rings: Dict[str, _Ring] = {}   # sym -> ring
        self._nudges: deque[Tuple[int,int]] = deque()   # (ts_ms, n)
        self._cancels: deque[Tuple[int,int]] = deque()
        self._last_report_ms: int = 0

    def _ring(self, sym: str) -> _Ring:
        r = self._rings.get(sym)
        if r is None:
            r = _Ring(self.cfg.window_sec * 1000)
            self._rings[sym] = r
        return r

    @staticmethod
    def _side_norm_slip_bps(side: str, fill_px: float, mark_px: float) -> float:
        # BUY: (fill - mark)/mark, SELL: (mark - fill)/mark → bps
        if mark_px <= 0.0 or fill_px <= 0.0:
            return 0.0
        if (side or "").upper() == "BUY":
            slip = (fill_px - mark_px) / mark_px
        elif (side or "").upper() == "SELL":
            slip = (mark_px - fill_px) / mark_px
        else:
            slip = abs(fill_px - mark_px) / mark_px
        return float(slip * 10000.0)

    def ingest_fill(self, symbol: str, side: str, qty: float, fill_px: float, mark_px: float, ts_ms: Optional[int] = None) -> None:
        ts = int(ts_ms or time.time() * 1000)
        bps = self._side_norm_slip_bps(side, fill_px, mark_px)
        self._ring(symbol).push(ts, bps)
        log.debug("[EQ] fill %s %s bps=%.2f", symbol, side, bps)

    def ingest_nudges(self, n: int, ts_ms: Optional[int] = None) -> None:
        self._nudges.append((int(ts_ms or time.time()*1000), int(n)))

    def ingest_cancels(self, n: int, ts_ms: Optional[int] = None) -> None:
        self._cancels.append((int(ts_ms or time.time()*1000), int(n)))

    def _sum_windowed(self, q: deque[Tuple[int,int]], now_ms: int) -> int:
        cut = now_ms - self.cfg.window_sec * 1000
        while q and q[0][0] < cut:
            q.popleft()
        return sum(n for _, n in q)

    def summary(self, now_ms: Optional[int] = None) -> Dict[str, Any]:
        now = int(now_ms or time.time()*1000)
        sym_stats: Dict[str, Dict[str, Any]] = {}
        all_vals: List[float] = []
        total_samples = 0
        for sym, ring in self._rings.items():
            vals = ring.values(now)
            if not vals:
                continue
            total_samples += len(vals)
            all_vals.extend(vals)
            sym_stats[sym] = self._stats(vals)
        nudges = self._sum_windowed(self._nudges, now)
        cancels = self._sum_windowed(self._cancels, now)
        overall = self._stats(all_vals) if all_vals else {"n": 0}

        return {
            "window_sec": self.cfg.window_sec,
            "samples": total_samples,
            "slip_bps_overall": overall,   # {"n","avg","p50","p90","max"}
            "nudges": nudges,
            "cancels": cancels,
            "symbols": sym_stats,
            "ts": now,
        }

    def _stats(self, xs: List[float]) -> Dict[str, Any]:
        if not xs:
            return {"n": 0}
        xs_sorted = sorted(xs)
        n = len(xs_sorted)
        avg = sum(xs_sorted)/n
        p50 = xs_sorted[n//2] if n%2==1 else 0.5*(xs_sorted[n//2-1]+xs_sorted[n//2])
        p90_idx = max(0, min(n-1, int(math.ceil(0.90*n)-1)))
        p90 = xs_sorted[p90_idx]
        mx = xs_sorted[-1]
        return {"n": n, "avg": avg, "p50": p50, "p90": p90, "max": mx}

# 싱글톤 헬퍼(앱에서 공유)
_EQ_SINGLETON: Optional[ExecQualityReporter] = None

def get_exec_quality(cfg: Optional[ExecQConfig] = None) -> ExecQualityReporter:
    global _EQ_SINGLETON
    if _EQ_SINGLETON is None:
        _EQ_SINGLETON = ExecQualityReporter(cfg or ExecQConfig())
    return _EQ_SINGLETON
