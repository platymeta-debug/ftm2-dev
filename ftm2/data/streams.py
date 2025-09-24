# -*- coding: utf-8 -*-
"""
Market Streams Manager
- kline / markPrice / user stream(listenKey)
- BinanceClient.subscribe_* 핸들을 관리하고, 수신 데이터를 StateBus 에 반영
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import List, Dict, Any, Optional

from ftm2.exchange.binance import BinanceClient, WSHandle, ws_stop_all_parallel
from ftm2.core.state import StateBus

log = logging.getLogger("ftm2.streams")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

try:  # pragma: no cover - optional dependency
    from websocket import WebSocketApp  # type: ignore
except Exception:  # pragma: no cover
    WebSocketApp = None  # type: ignore


def _env_bool(val: Optional[str], default: bool = False) -> bool:
    if val is None:
        return default
    v = val.strip().lower()
    if not v:
        return default
    return v in {"1", "true", "t", "y", "yes", "on"}


# [ANCHOR:STREAMS]
class UserStreamManager:
    """Manage Binance user data stream lifecycle (listenKey + WS)."""

    def __init__(self, bus: StateBus, client: BinanceClient) -> None:
        self.bus = bus
        self.client = client
        self.listen_key: Optional[str] = None
        self.ws: Optional[WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self._runner: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_keepalive = 0.0
        self.keepalive_sec = self._parse_int(os.getenv("LISTENKEY_REFRESH_SEC"), 1500)
        self.backoff_steps = self._parse_backoff(os.getenv("WS_RECONNECT_BACKOFF", "3,10,30"))
        self.boot_hydrate = _env_bool(os.getenv("POSITIONS_BOOT_HYDRATE", "true"), True)

    @staticmethod
    def _parse_int(value: Optional[str], default: int) -> int:
        if value is None or value == "":
            return default
        try:
            return max(0, int(float(value)))
        except Exception:
            return default

    @staticmethod
    def _parse_backoff(raw: Optional[str]) -> List[int]:
        vals: List[int] = []
        for part in (raw or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                vals.append(max(1, int(float(part))))
            except Exception:
                continue
        return vals or [3, 10, 30]

    def start(self) -> None:
        if self._runner and self._runner.is_alive():
            return
        self._stop.clear()
        self._runner = threading.Thread(target=self._run, name="user-stream", daemon=True)
        self._runner.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_ws()
        if self._runner and self._runner.is_alive():
            self._runner.join(timeout=2.0)
        self._runner = None

    def _run(self) -> None:
        try:
            self._hydrate_positions()
        except Exception:
            log.exception("[USER_STREAM] hydrate failed")

        backoff_idx = 0
        while not self._stop.is_set():
            if not self._ensure_listen_key():
                delay = self._delay(backoff_idx)
                backoff_idx = min(backoff_idx + 1, len(self.backoff_steps) - 1)
                log.warning("[USER_STREAM] listenKey unavailable retry_in=%ss", delay)
                self._stop.wait(delay)
                continue

            if not self._open_ws():
                self.listen_key = None
                delay = self._delay(backoff_idx)
                backoff_idx = min(backoff_idx + 1, len(self.backoff_steps) - 1)
                self._stop.wait(delay)
                continue

            backoff_idx = 0
            lost_key = False
            while not self._stop.wait(1.0):
                if not self._keepalive():
                    lost_key = True
                    break
                if self.ws_thread and not self.ws_thread.is_alive():
                    log.warning("[USER_STREAM] ws thread stopped")
                    break

            self._close_ws()
            if lost_key:
                self.listen_key = None

            if self._stop.is_set():
                break

            delay = self._delay(backoff_idx)
            backoff_idx = min(backoff_idx + 1, len(self.backoff_steps) - 1)
            self._stop.wait(delay)

    def _delay(self, idx: int) -> float:
        if not self.backoff_steps:
            return 3.0
        return float(self.backoff_steps[min(idx, len(self.backoff_steps) - 1)])

    def _hydrate_positions(self) -> None:
        if not self.boot_hydrate:
            return
        try:
            res = self.client.get_positions()
        except Exception as exc:
            log.warning("[USER_STREAM] hydrate error: %s", exc)
            return
        if not res.get("ok"):
            log.warning("[USER_STREAM] hydrate failed: %s", res.get("error"))
            return
        arr = res.get("data") or []
        positions: Dict[str, Dict[str, Any]] = {}
        for row in arr:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            try:
                qty = float(row.get("positionAmt", 0.0))
                ep = float(row.get("entryPrice", 0.0))
                up = float(row.get("unrealizedProfit") or row.get("unRealizedProfit") or 0.0)
            except Exception:
                qty = ep = up = 0.0
            positions[sym] = {
                "symbol": sym,
                "pa": qty,
                "ep": ep,
                "up": up,
                "mt": row.get("marginType"),
            }
        if positions:
            try:
                self.bus.set_positions(positions)
            except Exception:
                log.exception("[USER_STREAM] set_positions error")
            else:
                log.info("[USER_STREAM] hydrated positions n=%d", len(positions))

    def _ensure_listen_key(self) -> bool:
        if self.listen_key:
            return True
        try:
            res = self.client.get_listen_key()
        except Exception as exc:
            log.error("[USER_STREAM] listenKey request error: %s", exc)
            return False
        if res.get("ok"):
            data = res.get("data") or {}
            key = data.get("listenKey") if isinstance(data, dict) else None
            if key:
                self.listen_key = key
                self._last_keepalive = time.time()
                log.info("[USER_STREAM] listenKey=%s", key)
                return True
        log.error("[USER_STREAM] listenKey failed: %s", res.get("error"))
        return False

    def _keepalive(self) -> bool:
        if not self.listen_key:
            return False
        if self.keepalive_sec <= 0:
            return True
        if time.time() - self._last_keepalive < self.keepalive_sec:
            return True
        try:
            res = self.client.keepalive_listen_key(self.listen_key)
        except Exception as exc:
            log.warning("[USER_STREAM] keepalive exception: %s", exc)
            res = {"ok": False, "error": {"msg": str(exc)}}
        if res.get("ok"):
            self._last_keepalive = time.time()
            return True
        log.warning("[USER_STREAM] keepalive failed: %s", res.get("error"))
        self.listen_key = None
        return False

    def _open_ws(self) -> bool:
        if not self.listen_key:
            return False
        if WebSocketApp is None:
            log.error("[USER_STREAM] websocket-client not installed")
            return False
        url = f"{self.client.ws_base}/ws/{self.listen_key}"

        def _on_message(_ws, message: str) -> None:
            try:
                data = json.loads(message)
            except Exception:
                log.debug("[USER_STREAM] decode fail: %s", message[:120])
                return
            self._handle_event(data)

        def _on_error(_ws, error) -> None:  # pragma: no cover
            log.warning("[USER_STREAM] ws error: %s", error)

        def _on_close(_ws, status_code, msg) -> None:  # pragma: no cover
            log.info("[USER_STREAM] ws close code=%s msg=%s", status_code, msg)

        app = WebSocketApp(url, on_message=_on_message, on_error=_on_error, on_close=_on_close)
        self.ws = app

        def _runner() -> None:
            try:
                app.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as exc:  # pragma: no cover
                log.warning("[USER_STREAM] ws run error: %s", exc)

        self.ws_thread = threading.Thread(target=_runner, name=f"user-ws:{self.listen_key}", daemon=True)
        self.ws_thread.start()
        log.info("[USER_STREAM] ws connect url=%s", url)
        return True

    def _close_ws(self) -> None:
        app = self.ws
        self.ws = None
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        thread = self.ws_thread
        self.ws_thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _handle_event(self, event: Dict[str, Any]) -> None:
        etype = (event.get("e") or "").upper()
        if etype == "ORDER_TRADE_UPDATE":
            self._handle_order_update(event)
        elif etype == "ACCOUNT_UPDATE":
            self._handle_account_update(event)

    def _handle_order_update(self, event: Dict[str, Any]) -> None:
        order = event.get("o") or {}
        rec = {
            "symbol": (order.get("s") or "").upper(),
            "side": order.get("S"),
            "execType": order.get("x"),
            "status": order.get("X"),
            "lastQty": float(order.get("l", 0.0)),
            "lastPrice": float(order.get("L", 0.0)),
            "cumQty": float(order.get("Z", 0.0)),
            "avgPrice": float(order.get("ap", 0.0)),
            "orderId": order.get("i"),
            "clientOrderId": order.get("c"),
            "commission": float(order.get("n", 0.0)),
            "ts": int(event.get("E") or order.get("T") or 0),
        }
        try:
            self.bus.push_fill(rec)
        except Exception:
            log.exception("[USER_STREAM] push_fill error")
        else:
            log.info("[USER_STREAM] fill %s %s status=%s", rec.get("symbol"), rec.get("side"), rec.get("status"))

    def _handle_account_update(self, event: Dict[str, Any]) -> None:
        payload = event.get("a") or {}
        pos_map: Dict[str, Dict[str, Any]] = {}
        up_sum = 0.0
        for row in payload.get("P", []):
            sym = (row.get("s") or "").upper()
            if not sym:
                continue
            try:
                qty = float(row.get("pa", 0.0))
                ep = float(row.get("ep", 0.0))
                up = float(row.get("up", 0.0))
            except Exception:
                qty = ep = up = 0.0
            pos_map[sym] = {
                "symbol": sym,
                "pa": qty,
                "ep": ep,
                "up": up,
                "mt": row.get("mt"),
            }
            up_sum += up
        if pos_map:
            try:
                self.bus.set_positions(pos_map)
            except Exception:
                log.exception("[USER_STREAM] set_positions error")

        bal = None
        for item in payload.get("B", []):
            if (item.get("a") or "").upper() == "USDT":
                bal = item
                break
        if bal:
            try:
                wallet = float(bal.get("wb", 0.0))
                avail = float(bal.get("cw", 0.0))
                equity = wallet + up_sum
            except Exception:
                wallet = avail = equity = 0.0
            account = {
                "ccy": "USDT",
                "totalWalletBalance": wallet,
                "availableBalance": avail,
                "totalUnrealizedProfit": up_sum,
                "totalMarginBalance": equity,
                "wallet": wallet,
                "avail": avail,
                "upnl": up_sum,
                "equity": equity,
            }
            try:
                self.bus.set_account(account)
            except Exception:
                log.exception("[USER_STREAM] set_account error")
            else:
                log.info(
                    "[USER_STREAM] account wallet=%.2f upnl=%.2f equity=%.2f avail=%.2f",
                    wallet,
                    up_sum,
                    equity,
                    avail,
                )


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

        self.user_stream: Optional[UserStreamManager] = None

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

        if self.use_user and self.user_cli and getattr(self.user_cli, "key", ""):
            self.user_stream = UserStreamManager(self.bus, self.user_cli)
            self.user_stream.start()
        else:
            log.info("[USER_STREAM] disabled (no key or use_user=False)")

    def stop(self) -> None:
        self._stop.set()
        if self.user_stream:
            try:
                self.user_stream.stop()
            except Exception:
                log.exception("[USER_STREAM] stop error")
            self.user_stream = None
        # stop ws handles
        ws_stop_all_parallel()
        self._handles.clear()

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
            if not bar.get("x"):
                return
            # [ANCHOR:WS_ON_KLINE] begin
            if hasattr(self, "feature_engine"):
                self.feature_engine.update(sym, itv, self.bus)
            if hasattr(self, "regime"):
                self.regime.update(sym, itv, self.bus)
            if hasattr(self, "orch"):
                self.orch.on_bar_close(sym, itv, self.bus)
            # [ANCHOR:WS_ON_KLINE] end
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

    # ---------------------- internals ----------------------
