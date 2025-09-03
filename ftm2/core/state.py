# -*- coding: utf-8 -*-
"""
스레드-세이프 전역 StateBus
- marks: {symbol: {"price": float, "time": int}}
- klines: {(symbol, interval): last_bar_dict}
- positions: {symbol: {..}}
- account: {...}
"""
from __future__ import annotations
import threading
import time
from collections import deque
from typing import Dict, Tuple, Any, List


# [ANCHOR:STATE_BUS]
class StateBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._marks: Dict[str, Dict[str, Any]] = {}
        self._klines: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._account: Dict[str, Any] = {}
        self._features: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._regimes: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._forecasts: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._targets: Dict[str, Dict[str, Any]] = {}
        self._risk: Dict[str, Any] = {}
        self._fills = deque(maxlen=1000)   # 체결 이벤트 큐
        self._open_orders: Dict[str, List[Dict[str, Any]]] = {}   # 심볼 → 오더 리스트
        self._guard: Dict[str, Any] = {}
        self._boot_ts = int(time.time() * 1000)

    # --- updates
    def update_mark(self, symbol: str, price: float, ts_ms: int) -> None:
        with self._lock:
            self._marks[symbol] = {"price": float(price), "time": int(ts_ms)}

    def update_kline(self, symbol: str, interval: str, bar: Dict[str, Any]) -> None:
        with self._lock:
            self._klines[(symbol, interval)] = dict(bar)

    def set_positions(self, positions: Dict[str, Dict[str, Any]]) -> None:
        with self._lock:
            self._positions = dict(positions)

    def set_account(self, account: Dict[str, Any]) -> None:
        with self._lock:
            self._account = dict(account)

    def update_features(self, symbol: str, interval: str, feats: Dict[str, Any]) -> None:
        with self._lock:
            self._features[(symbol, interval)] = dict(feats)

    def update_regime(self, symbol: str, interval: str, regime: Dict[str, Any]) -> None:
        with self._lock:
            self._regimes[(symbol, interval)] = dict(regime)

    def update_forecast(self, symbol: str, interval: str, fc: Dict[str, Any]) -> None:
        with self._lock:
            self._forecasts[(symbol, interval)] = dict(fc)

    def set_targets(self, mapping: Dict[str, Dict[str, Any]]) -> None:
        with self._lock:
            self._targets = dict(mapping)

    def set_risk_state(self, state: Dict[str, Any]) -> None:
        with self._lock:
            self._risk = dict(state)

    # --- NEW: fills ---
    def push_fill(self, fill: Dict[str, Any]) -> None:
        with self._lock:
            self._fills.append(dict(fill))

    def drain_fills(self, max_n: int = 200) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._lock:
            for _ in range(min(max_n, len(self._fills))):
                out.append(self._fills.popleft())
        return out

    def set_open_orders(self, mapping: Dict[str, List[Dict[str, Any]]]) -> None:
        with self._lock:
            self._open_orders = {k: list(v) for k, v in (mapping or {}).items()}


    def set_guard_state(self, state: Dict[str, Any]) -> None:
        with self._lock:
            self._guard = dict(state)




    # --- reads
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "marks": dict(self._marks),
                "klines": dict(self._klines),
                "positions": dict(self._positions),
                "account": dict(self._account),
                "features": dict(self._features),
                "regimes": dict(self._regimes),
                "forecasts": dict(self._forecasts),
                "targets": dict(self._targets),
                "risk": dict(self._risk),
                "open_orders": {k: list(v) for k, v in self._open_orders.items()},
                "guard": dict(self._guard),
                "boot_ts": self._boot_ts,
                "now_ts": int(time.time() * 1000),
            }

    def uptime_s(self) -> int:
        return int(time.time() - (self._boot_ts / 1000.0))
