from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from ftm2.exchange.binance import BinanceClient

log = logging.getLogger("ftm2.streams")


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except Exception:
        return default


KLINE_LIMIT_1M = _env_int("KLINE_LIMIT_1M", 1500)
KLINE_LIMIT_OTHERS = _env_int("KLINE_LIMIT_OTHERS", 600)
SKEW_TOL = _env_int("STREAM_CLOCK_SKEW_TOL_MS", 1500)

TFs = ["1m", "5m", "15m", "1h", "4h"]


@dataclass
class State:
    kline_map: Dict[str, Dict[str, Deque[dict]]] = field(
        default_factory=lambda: defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=KLINE_LIMIT_OTHERS))
        )
    )
    mark: Dict[str, dict] = field(default_factory=dict)
    account: Dict[str, List[dict]] = field(default_factory=lambda: {"balances": [], "positions": []})


class StreamManager:
    """Manage REST bootstrap and websocket streaming into State."""

    def __init__(self, cli: BinanceClient, state: State) -> None:
        self.cli = cli
        self.state = state
        self._running = False

    # ------------------------------------------------------------------
    # WS handling
    # ------------------------------------------------------------------
    def _within_skew(self, ts: Optional[int]) -> bool:
        if ts is None:
            return True
        try:
            ts_int = int(ts)
        except Exception:
            return True
        now_ms = int(time.time() * 1000)
        if abs(now_ms - ts_int) > SKEW_TOL:
            log.debug("STRM.DROP.SKEW ts=%s now=%s", ts_int, now_ms)
            return False
        return True

    def _on_ws(self, msg: dict) -> None:
        try:
            data = msg.get("data") or {}
            stream = (msg.get("stream") or "").lower()
            event = (data.get("e") or "").lower()

            if "kline" in stream or event == "kline":
                k = data.get("k") or {}
                sym = (k.get("s") or "").upper()
                tf = k.get("i")
                if not sym or tf not in TFs:
                    return
                if not bool(k.get("x")):
                    return
                ts = int(k.get("t", 0))
                if not self._within_skew(ts):
                    return
                item = {
                    "ts": ts,
                    "o": float(k.get("o", 0.0)),
                    "h": float(k.get("h", 0.0)),
                    "l": float(k.get("l", 0.0)),
                    "c": float(k.get("c", 0.0)),
                    "v": float(k.get("v", 0.0)),
                    "tf": tf,
                    "symbol": sym,
                }
                dq = self.state.kline_map[sym][tf]
                if tf == "1m" and dq.maxlen != KLINE_LIMIT_1M:
                    dq = self.state.kline_map[sym][tf] = deque(maxlen=KLINE_LIMIT_1M)
                if dq and item["ts"] <= dq[-1]["ts"]:
                    log.debug("STRM.DROP.OUTDATED %s %s %s<=%s", sym, tf, item["ts"], dq[-1]["ts"])
                    return
                dq.append(item)
                return

            if "markprice" in stream:
                sym = stream.split("@")[0].upper()
                ts = int(data.get("E", 0))
                if not self._within_skew(ts):
                    return
                mark = float(data.get("p", data.get("markPrice", 0.0)))
                prev = self.state.mark.get(sym)
                if prev and ts <= int(prev.get("ts", 0)):
                    log.debug("STRM.DROP.OUTDATED %s mark ts=%s<=%s", sym, ts, prev.get("ts"))
                    return
                self.state.mark[sym] = {"ts": ts or _now_ms(), "mark": mark}
                return

            if stream.startswith("!userdata") or event in {"account_update", "order_trade_update"}:
                etype = event
                if etype == "account_update":
                    acct = data.get("a") or {}
                    balances = []
                    for b in acct.get("B", []):
                        try:
                            balances.append(
                                {
                                    "asset": b.get("a"),
                                    "wb": float(b.get("wb", 0.0)),
                                    "cw": float(b.get("cw", b.get("wb", 0.0))),
                                }
                            )
                        except Exception:
                            continue
                    positions = []
                    for p in acct.get("P", []):
                        try:
                            pa = float(p.get("pa", 0.0))
                            ep = float(p.get("ep", 0.0))
                            up = float(p.get("up", p.get("unRealizedProfit", 0.0)))
                        except Exception:
                            pa = ep = up = 0.0
                        positions.append(
                            {
                                "symbol": (p.get("s") or "").upper(),
                                "pa": pa,
                                "ep": ep,
                                "up": up,
                                "mt": p.get("mt"),
                            }
                        )
                    self.state.account = {"balances": balances, "positions": positions}
        except Exception as exc:
            log.warning("STRM.WS.PARSE %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self, symbols: List[str]) -> None:
        self._running = True
        streams: List[str] = []
        for sym in symbols:
            sym_l = sym.lower()
            streams.extend(
                [
                    f"{sym_l}@kline_1m",
                    f"{sym_l}@kline_5m",
                    f"{sym_l}@kline_15m",
                    f"{sym_l}@kline_1h",
                    f"{sym_l}@kline_4h",
                    f"{sym_l}@markPrice@1s",
                ]
            )
        self.cli.ws_subscribe(streams, self._on_ws)

    def stop(self) -> None:
        self._running = False
        self.cli.ws_close()

    def preload(
        self,
        symbols: List[str],
        limit_1m: int = KLINE_LIMIT_1M,
        limit_others: int = KLINE_LIMIT_OTHERS,
    ) -> None:
        for sym in symbols:
            for tf in TFs:
                lim = limit_1m if tf == "1m" else limit_others
                rows = self.cli.get_klines(sym, tf, limit=lim)
                dq = self.state.kline_map[sym][tf]
                dq.clear()
                maxlen = KLINE_LIMIT_1M if tf == "1m" else KLINE_LIMIT_OTHERS
                if dq.maxlen != maxlen:
                    dq = self.state.kline_map[sym][tf] = deque(maxlen=maxlen)
                for row in rows:
                    dq.append(row)
            try:
                mark = self.cli.get_mark_price(sym)
            except Exception:
                mark = {"markPrice": 0.0, "time": int(time.time() * 1000)}
            self.state.mark[sym] = {"ts": int(mark["time"]), "mark": float(mark["markPrice"])}


def _now_ms() -> int:
    return int(time.time() * 1000)

