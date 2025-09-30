from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import threading
import time
import urllib.parse as up
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests

log = logging.getLogger("ftm2.binance")

BINANCE_FUTURES_LIVE = "https://fapi.binance.com"
BINANCE_FUTURES_TEST = "https://testnet.binancefuture.com"
WS_COMBINED_LIVE = "wss://fstream.binance.com/stream?streams="
WS_COMBINED_TEST = "wss://stream.binancefuture.com/stream?streams="


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    return v if v not in (None, "") else default


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _Resp:
    ok: bool
    data: Any
    code: Optional[int] = None
    msg: str = ""


class BinanceClient:
    """Simple USDⓈ-M Futures REST/WS connector.

    Attributes
    ----------
    mode: str
        Either "testnet" or "live". If not supplied the mode is derived from
        ``TRADE_MODE`` → ``MODE`` → ``DATA_MODE`` environment variables with a
        fallback to ``testnet``.
    base: str
        REST base URL resolved from the mode.
    ws_base: str
        Combined stream websocket base URL.
    key / secret: str
        API credentials resolved from explicit arguments or environment
        variables. The resolver honours the following order per value:
        explicit argument → ``BINANCE__API_KEY``/``BINANCE__API_SECRET`` →
        environment scoped pairs (``BINANCE_TESTNET_API_KEY`` etc.) → generic
        ``BINANCE_API_KEY`` pair.
    recv: int
        Binance ``recvWindow`` in milliseconds.
    timeout: int
        REST/WS timeout seconds.
    """

    def __init__(
        self,
        mode: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        recv_window_ms: int = 5000,
        timeout_s: int = 10,
    ) -> None:
        env_mode = _env("TRADE_MODE") or _env("MODE") or _env("DATA_MODE")
        raw_mode = (mode or env_mode or "testnet").lower()
        if raw_mode == "dry":
            raw_mode = "testnet"
        self.mode = "live" if raw_mode == "live" else "testnet"

        self.base = BINANCE_FUTURES_TEST if self.mode == "testnet" else BINANCE_FUTURES_LIVE
        self.ws_base = WS_COMBINED_TEST if self.mode == "testnet" else WS_COMBINED_LIVE

        self.key, self.secret = self._resolve_credentials(api_key, api_secret)
        self.recv = int(recv_window_ms)
        self.timeout = int(timeout_s)

        self._ws_th: Optional[threading.Thread] = None
        self._ws_stop = threading.Event()
        self._ws = None
        self._poll_ctl: Optional[queue.Queue] = None

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------
    def _resolve_credentials(self, api_key: Optional[str], api_secret: Optional[str]) -> tuple[str, str]:
        if api_key and api_secret:
            return api_key, api_secret

        scoped_prefix = "LIVE" if self.mode == "live" else "TESTNET"
        scope_key = _env(f"BINANCE_{scoped_prefix}_API_KEY")
        scope_secret = _env(f"BINANCE_{scoped_prefix}_API_SECRET")

        generic_key = _env("BINANCE_API_KEY")
        generic_secret = _env("BINANCE_API_SECRET")
        double_key = _env("BINANCE__API_KEY")
        double_secret = _env("BINANCE__API_SECRET")

        key = api_key or double_key or scope_key or generic_key or ""
        secret = api_secret or double_secret or scope_secret or generic_secret or ""
        return key, secret

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.secret:
            raise RuntimeError("API secret required for signed request")
        params = dict(params)
        query = up.urlencode(params, doseq=True)
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _r(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> _Resp:
        url = f"{self.base}{path}"
        headers: Dict[str, str] = {}
        params = dict(params or {})
        if auth:
            params.update({"timestamp": _now_ms(), "recvWindow": self.recv})
            params = self._sign(params)
            headers["X-MBX-APIKEY"] = self.key
        try:
            if method == "GET":
                r = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            elif method == "POST":
                r = requests.post(url, params=params, headers=headers, timeout=self.timeout)
            elif method == "DELETE":
                r = requests.delete(url, params=params, headers=headers, timeout=self.timeout)
            else:
                raise ValueError(method)
            if r.status_code == 429:
                log.warning("BX.REST.RATE_LIMIT %s", r.text)
            r.raise_for_status()
            data = r.json() if r.text else None
            return _Resp(True, data)
        except requests.RequestException as exc:
            txt = getattr(exc.response, "text", str(exc))
            log.error("BX.REST.FAIL %s %s %s", method, path, txt)
            code = None
            msg = str(txt)
            if getattr(exc, "response", None) is not None:
                try:
                    payload = exc.response.json()
                    code = payload.get("code")
                    msg = payload.get("msg", msg)
                except Exception:
                    pass
            return _Resp(False, None, code=code, msg=msg)

    # ------------------------------------------------------------------
    # Public market/account
    # ------------------------------------------------------------------
    def get_exchange_info(self) -> dict:
        resp = self._r("GET", "/fapi/v1/exchangeInfo")
        if not resp.ok:
            raise RuntimeError(f"exchange_info_failed:{resp.code}:{resp.msg}")
        return resp.data or {}

    def get_mark_price(self, symbol: str) -> dict:
        resp = self._r("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        if not resp.ok:
            raise RuntimeError(f"mark_price_failed:{resp.code}:{resp.msg}")
        data = resp.data or {}
        return {
            "symbol": data.get("symbol", symbol.upper()),
            "markPrice": float(data.get("markPrice", 0.0)),
            "time": int(data.get("time", _now_ms())),
        }

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        end_ms: Optional[int] = None,
    ) -> List[dict]:
        limit = max(1, min(limit, 1500))
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_ms:
            params["endTime"] = end_ms
        resp = self._r("GET", "/fapi/v1/klines", params)
        if not resp.ok:
            raise RuntimeError(f"klines_failed:{resp.code}:{resp.msg}")
        rows = resp.data or []
        out: List[dict] = []
        for row in rows:
            try:
                out.append(
                    {
                        "ts": int(row[0]),
                        "o": float(row[1]),
                        "h": float(row[2]),
                        "l": float(row[3]),
                        "c": float(row[4]),
                        "v": float(row[5]),
                        "tf": interval,
                        "symbol": symbol,
                    }
                )
            except Exception:
                continue
        return out

    def get_position_risk(self, symbol: Optional[str] = None) -> List[dict]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        resp = self._r("GET", "/fapi/v2/positionRisk", params, auth=True)
        if not resp.ok:
            raise RuntimeError(f"position_risk_failed:{resp.code}:{resp.msg}")
        rows = resp.data or []
        out: List[dict] = []
        for row in rows:
            try:
                qty = float(row.get("positionAmt", 0.0))
            except Exception:
                qty = 0.0
            if abs(qty) < 1e-12:
                continue
            out.append(
                {
                    "symbol": (row.get("symbol") or symbol or "").upper(),
                    "qty": qty,
                    "entryPrice": float(row.get("entryPrice", 0.0)),
                    "unPnl": float(row.get("unRealizedProfit", row.get("unrealizedProfit", 0.0))),
                }
            )
        return out

    def get_balance(self) -> List[dict]:
        resp = self._r("GET", "/fapi/v2/balance", {}, auth=True)
        if not resp.ok:
            raise RuntimeError(f"balance_failed:{resp.code}:{resp.msg}")
        rows = resp.data or []
        out: List[dict] = []
        for row in rows:
            out.append(
                {
                    "asset": row.get("asset"),
                    "wb": float(row.get("balance", row.get("wb", 0.0))),
                    "cw": float(row.get("crossWalletBalance", row.get("cw", row.get("balance", 0.0)))),
                }
            )
        return out

    # [ANCHOR:SET_LEVERAGE]
    def set_leverage(self, symbol: str, leverage: int) -> dict:
        payload = {"symbol": symbol.upper(), "leverage": int(leverage)}
        resp = self._r("POST", "/fapi/v1/leverage", payload, auth=True)
        if not resp.ok:
            raise RuntimeError(f"leverage_set_fail:{resp.code}:{resp.msg}")
        return resp.data or {}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def create_order(
        self,
        symbol: str,
        side: str,
        type: str,
        qty: float,
        price: Optional[float] = None,
        reduce_only: bool = False,
        client_id: Optional[str] = None,
    ) -> dict:
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": type.upper(),
            "quantity": f"{qty:.20f}",
        }
        if client_id:
            payload["newClientOrderId"] = client_id
        if reduce_only:
            payload["reduceOnly"] = "true"
        if payload["type"] == "LIMIT":
            if price is None:
                raise ValueError("price required for LIMIT orders")
            payload.update({"price": f"{price:.8f}", "timeInForce": "GTC"})
        resp = self._r("POST", "/fapi/v1/order", payload, auth=True)
        if not resp.ok:
            raise RuntimeError(f"order_reject:{resp.code}:{resp.msg}")
        log.info("BX.ORD.SENT %s %s qty=%s", symbol, side.upper(), payload["quantity"])
        return resp.data or {}

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_id: Optional[str] = None,
    ) -> dict:
        payload: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            payload["orderId"] = order_id
        if client_id is not None:
            payload["origClientOrderId"] = client_id
        resp = self._r("DELETE", "/fapi/v1/order", payload, auth=True)
        if not resp.ok:
            raise RuntimeError(f"order_cancel_fail:{resp.code}:{resp.msg}")
        log.info("BX.ORD.CANCEL %s %s", symbol, order_id or client_id)
        return resp.data or {}

    # ------------------------------------------------------------------
    # Websocket
    # ------------------------------------------------------------------
    def ws_subscribe(self, streams: List[str], on_msg: Callable[[dict], None]) -> None:
        """Subscribe to combined websocket streams.

        If ``websocket-client`` is not installed we fall back to a light-weight
        REST polling loop that mimics kline/mark streams every second.
        """

        try:
            import websocket  # type: ignore
        except Exception:
            log.warning("BX.WS.MISSING websocket-client -> REST polling fallback")
            self._start_poll_fallback(streams, on_msg)
            return

        self._ws_stop.clear()
        url = self.ws_base + "/".join(streams)

        def _run() -> None:
            while not self._ws_stop.is_set():
                try:
                    self._ws = websocket.create_connection(url, timeout=self.timeout)
                    log.info("BX.WS.CONNECT %s", url)
                    while not self._ws_stop.is_set():
                        raw = self._ws.recv()
                        if not raw:
                            continue
                        data = json.loads(raw)
                        on_msg(data)
                except Exception as exc:
                    log.warning("BX.WS.RETRY %s", exc)
                    time.sleep(1.0)
                finally:
                    try:
                        if self._ws:
                            self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
                    if not self._ws_stop.is_set():
                        log.info("BX.WS.RECONNECT")

        self._ws_th = threading.Thread(target=_run, name="bx-ws", daemon=True)
        self._ws_th.start()

    def _start_poll_fallback(self, streams: List[str], on_msg: Callable[[dict], None]) -> None:
        self._ws_stop.clear()
        desired: Dict[str, Any] = {"kline": [], "mark": []}
        for stream in streams:
            if "@kline_" in stream:
                sym, tf = stream.split("@kline_")
                desired["kline"].append((sym.upper(), tf))
            elif "@markPrice" in stream:
                sym = stream.split("@")[0].upper()
                desired["mark"].append(sym)

        def _loop() -> None:
            while not self._ws_stop.is_set():
                start = time.time()
                for sym, tf in desired["kline"]:
                    try:
                        klines = self.get_klines(sym, tf, limit=2)
                    except Exception as exc:
                        log.warning("BX.WS.POLL_FAIL %s %s", sym, exc)
                        continue
                    if not klines:
                        continue
                    k = klines[-1]
                    payload = {
                        "stream": f"{sym.lower()}@kline_{tf}",
                        "data": {
                            "e": "kline",
                            "E": _now_ms(),
                            "k": {
                                "t": k["ts"],
                                "T": k["ts"] + 1,
                                "s": sym,
                                "i": tf,
                                "f": 0,
                                "L": 0,
                                "o": str(k["o"]),
                                "c": str(k["c"]),
                                "h": str(k["h"]),
                                "l": str(k["l"]),
                                "v": str(k["v"]),
                                "x": False,
                            },
                        },
                    }
                    on_msg(payload)
                for sym in desired["mark"]:
                    try:
                        mark = self.get_mark_price(sym)
                    except Exception as exc:
                        log.warning("BX.WS.POLL_FAIL %s %s", sym, exc)
                        continue
                    payload = {
                        "stream": f"{sym.lower()}@markPrice@1s",
                        "data": {
                            "E": mark["time"],
                            "p": str(mark["markPrice"]),
                            "s": sym,
                        },
                    }
                    on_msg(payload)
                delay = max(0.5, 1.0 - (time.time() - start))
                time.sleep(delay)

        self._ws_th = threading.Thread(target=_loop, name="bx-poll", daemon=True)
        self._ws_th.start()

    def ws_close(self) -> None:
        self._ws_stop.set()
        if self._ws_th and self._ws_th.is_alive():
            self._ws_th.join(timeout=2.0)
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        self._ws_th = None

