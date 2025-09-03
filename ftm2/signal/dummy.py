# -*- coding: utf-8 -*-
"""
더미 포캐스트: 닫힌 봉 기준 간단 방향 신호
- close > open → LONG, else SHORT
- score = (close - open) / max(open, 1e-9)
"""
from __future__ import annotations
from typing import Dict, Any, List, Tuple

# [ANCHOR:DUMMY_FORECAST]
class DummyForecaster:
    def __init__(self, symbols: List[str], interval: str) -> None:
        self.symbols = symbols
        self.interval = interval
        # 마지막으로 처리한 닫힌 봉의 T(종료시간)
        self._last_T: Dict[str, int] = {}

    def evaluate(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        snapshot: StateBus.snapshot()
        반환: 의도 신호 리스트 (닫힌 봉만 1회 방출)
        """
        out: List[Dict[str, Any]] = []
        klines: Dict[Tuple[str, str], Dict[str, Any]] = snapshot.get("klines", {})
        for sym in self.symbols:
            bar = klines.get((sym, self.interval))
            if not bar:
                continue
            # 닫힌 봉만 사용
            if not bar.get("x"):
                continue
            T = int(bar.get("T") or 0)
            if self._last_T.get(sym) == T:
                continue  # 이미 처리함
            self._last_T[sym] = T

            o = float(bar.get("o", 0.0))
            c = float(bar.get("c", 0.0))
            side = "LONG" if c >= o else "SHORT"
            score = (c - o) / (o if o else 1e-9)

            out.append({
                "symbol": sym,
                "side": side,
                "score": float(score),
                "reason": "DUMMY_KLINE",
                "kline_T": T,
            })
        return out
