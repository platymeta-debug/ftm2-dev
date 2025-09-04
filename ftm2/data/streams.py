# -*- coding: utf-8 -*-
"""
Market Streams Manager
- kline / markPrice / user stream(listenKey)
- BinanceClient.subscribe_* 핸들을 관리하고, 수신 데이터를 StateBus 에 반영
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Dict, Any, Optional

from ftm2.exchange.binance import BinanceClient, WSHandle
from ftm2.core.state import StateBus

log = logging.getLogger("ftm2.streams")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# [ANCHOR:STREAMS]
# [ANCHOR:DUAL_MODE]
class StreamManager:
    def __init__(
        self,
        data_client: BinanceClient,
        user_client: Optional[BinanceClient],
        bus: StateBus,
        symbols: List[str],
        kline_intervals: List[str],
        *,
        use_mark: bool = True,
        use_user: bool = True,
        rest_fallback: bool = True,
    ) -> None:
        """
        data_client: 공개 데이터(LIVE/TESTNET 상관없음)
        user_client: 유저스트림/계정 이벤트용(TRADE_MODE 기준). 없으면 미사용.
        """
        self.data_cli = data_client
        self.user_cli = user_client
        self.bus = bus
        self.symbols = symbols
        self.kline_intervals = kline_intervals
        self.use_mark = use_mark
        self.use_user = use_user
        self.rest_fallback = rest_fallback

        self._handles: List[WSHandle] = []
        self._poll_ths: List[threading.Thread] = []
        self._stop = threading.Event()

        # user stream
        self._listen_key: Optional[str] = None
        self._keepalive_th: Optional[threading.Thread] = None

    # ---------------------- public API ----------------------
    def start(self) -> None:
        # kline
        for sym in self.symbols:
            for itv in self.kline_intervals:
                h = self.data_cli.subscribe_kline(sym, itv, self._on_kline)
                if h.error:
                    log.warning("[WS HANDLE_ERR] kline %s %s %s", sym, itv, h.error)
                else:
                    self._handles.append(h)
        # mark price
        if self.use_mark:
            for sym in self.symbols:
                h = self.data_cli.subscribe_mark_price(sym, self._on_mark)
                if h.error:
                    log.warning("[WS HANDLE_ERR] markPrice %s %s", sym, h.error)
                    code = h.error.get("error", {}).get("code") if h.error else None
                    if code == "E_WS_DRIVER_MISSING" and self.rest_fallback:
                        t = threading.Thread(target=self._poll_mark,
                                             args=(sym,), name=f"mark-poll:{sym}",
                                             daemon=True)
                        t.start()
                        self._poll_ths.append(t)
                else:
                    self._handles.append(h)

        log.info("[WS START] kline=%s mark=%s symbols=%d",
                 ",".join(self.kline_intervals), self.use_mark, len(self.symbols))

        # user stream
        if self.use_user and self.user_cli and self.user_cli.key and self.user_cli.secret:
            r = self.user_cli.start_user_stream()
            if r.get("ok"):
                self._listen_key = r["data"].get("listenKey")
                if self._listen_key:
                    h = self.user_cli.subscribe_user(self._listen_key, self._on_user)
                    if h.error:
                        log.warning("[WS HANDLE_ERR] user %s", h.error)
                    self._handles.append(h)
                    log.info("[LISTENKEY] started key=%s", self._listen_key)

                    # keepalive 25분 주기
                    self._keepalive_th = threading.Thread(target=self._keepalive_loop,
                                                          name="listenKey-keepalive", daemon=True)
                    self._keepalive_th.start()
            else:
                log.warning("[USER_STREAM] cannot start: %s", r.get("error"))
        else:
            log.info("[USER_STREAM] disabled (no key/secret or use_user=False)")

    def stop(self) -> None:
        self._stop.set()
        # close user stream first
        if self._listen_key and self.user_cli:
            try:
                self.user_cli.close_user_stream(self._listen_key)
            except Exception:
                pass
        # stop ws handles
        for h in list(self._handles):
            try:
                h.stop(timeout=2.0)
            except Exception:
                pass
        self._handles.clear()

        if self._keepalive_th and self._keepalive_th.is_alive():
            self._keepalive_th.join(timeout=2.0)
        for t in list(self._poll_ths):
            if t.is_alive():
                t.join(timeout=2.0)
        self._poll_ths.clear()
        log.info("[WS STOPPED] all streams closed")

    def stop_all(self) -> None:
        """Compat wrapper for graceful shutdown."""
        self.stop()


    def _poll_mark(self, symbol: str, interval_s: float = 1.0) -> None:
        while not self._stop.is_set():
            r = self.data_cli.mark_price(symbol)
            if r.get("ok"):
                d = r["data"]
                price = float(d.get("markPrice", 0.0))
                ts = int(d.get("time") or int(time.time() * 1000))
                self.bus.update_mark(symbol, price, ts)
                log.debug("[REST MARK] %s price=%s ts=%s", symbol, price, ts)
            time.sleep(interval_s)

    # ---------------------- callbacks ----------------------
    def _on_kline(self, msg: Dict[str, Any]) -> None:
        """
        msg 예(요약):
        {
          "e":"kline","E":1690000000000,"s":"BTCUSDT",
          "k":{"t":..., "T":..., "s":"BTCUSDT","i":"1m","o":"...","h":"...","l":"...","c":"...","v":"...", "x":false, ...}
        }
        """
        try:
            if (msg.get("e") or "").lower() != "kline":
                return
            k = msg.get("k") or {}
            sym = (k.get("s") or msg.get("s") or "").upper()
            itv = k.get("i") or ""
            bar = {
                "t": int(k.get("t", 0)),
                "T": int(k.get("T", 0)),
                "o": float(k.get("o", 0.0)),
                "h": float(k.get("h", 0.0)),
                "l": float(k.get("l", 0.0)),
                "c": float(k.get("c", 0.0)),
                "v": float(k.get("v", 0.0)),
                "x": bool(k.get("x", False)),
            }
            self.bus.update_kline(sym, itv, bar)
            log.debug("[WS KLINE] %s %s close=%s", sym, itv, bar["x"])
        except Exception as e:
            log.exception("kline cb err: %s", e)

    def _on_mark(self, msg: Dict[str, Any]) -> None:
        """
        futures markPrice stream:
        {"e":"markPriceUpdate","E":..., "s":"BTCUSDT","p":"<mark>","r":"<est funding>","T":..., ...}
        """
        try:
            sym = (msg.get("s") or "").upper()
            if not sym:
                return
            p = msg.get("p") or msg.get("markPrice") or 0.0
            ts = int(msg.get("E") or msg.get("T") or 0)
            self.bus.update_mark(sym, float(p), ts)
            log.debug("[WS MARK] %s price=%s ts=%s", sym, p, ts)
        except Exception as e:
            log.exception("mark cb err: %s", e)

    def _on_user(self, msg: Dict[str, Any]) -> None:
        """
        futures user data (요약):
        - ACCOUNT_UPDATE: {"e":"ACCOUNT_UPDATE","a":{"B":[... balances ...], "P":[... positions ...]}}
        - ORDER_TRADE_UPDATE: {"e":"ORDER_TRADE_UPDATE","o":{...}}
        """
        try:
            evt = (msg.get("e") or "").upper()
            if evt == "ACCOUNT_UPDATE":
                a = msg.get("a") or {}
                # positions array → {symbol: {...}} 로 단순 매핑
                pos_map: Dict[str, Dict[str, Any]] = {}
                for p in a.get("P", []):
                    sym = (p.get("s") or "").upper()
                    if not sym:
                        continue
                    pos_map[sym] = {
                        "symbol": sym,
                        "pa": float(p.get("pa", 0.0)),
                        "ep": float(p.get("ep", 0.0)),
                        "up": float(p.get("up", 0.0)),
                        "mt": p.get("mt"),
                    }
                if pos_map:
                    self.bus.set_positions(pos_map)
            elif evt == "ORDER_TRADE_UPDATE":
                o = msg.get("o") or {}
                rec = {
                    "symbol": (o.get("s") or "").upper(),
                    "side": o.get("S"),
                    "execType": o.get("x"),
                    "status": o.get("X"),
                    "lastQty": float(o.get("l", 0.0)),
                    "lastPrice": float(o.get("L", 0.0)),
                    "cumQty": float(o.get("Z", 0.0)),
                    "avgPrice": float(o.get("ap", 0.0)),
                    "orderId": o.get("i"),
                    "clientOrderId": o.get("c"),
                    "commission": float(o.get("n", 0.0)),
                    "ts": int(msg.get("E") or o.get("T") or 0),
                }
                self.bus.push_fill(rec)
                log.info("[USER_STREAM][OTU] %s %s %s %s", o.get("s"), o.get("S"), o.get("X"), o.get("ap"))
        except Exception as e:
            log.exception("user cb err: %s", e)

    # ---------------------- internals ----------------------
    def _keepalive_loop(self, period_s: int = 1500) -> None:
        """
        listenKey keepalive. 기본 25분(1500s).
        실패 시 새 키로 재시작을 시도한다.
        """
        while not self._stop.is_set():
            time.sleep(period_s)
            if self._stop.is_set():
                break
            if not self._listen_key:
                continue
            r = self.user_cli.keepalive_user_stream(self._listen_key) if self.user_cli else {"ok": False}
            if r.get("ok"):
                log.debug("[KEEPALIVE] ok key=%s", self._listen_key)
            else:
                log.warning("[KEEPALIVE] failed %s → try re-create", r.get("error"))
                # 재생성
                r2 = self.user_cli.start_user_stream() if self.user_cli else {"ok": False}
                if r2.get("ok"):
                    self._listen_key = r2["data"].get("listenKey")
                    log.info("[LISTENKEY] rotated key=%s", self._listen_key)
                else:
                    log.error("[LISTENKEY] rotate failed: %s", r2.get("error"))

