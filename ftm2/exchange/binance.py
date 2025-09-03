# -*- coding: utf-8 -*-
"""
Binance USDⓈ-M Futures Connector (testnet↔live toggle)

외부 의존:
- REST: httpx (권장) 또는 requests 폴백
- WS  : websocket-client (권장). 미설치 시 폴백 REST-폴링(간이) 제공.

모든 메서드는 표준 응답 계약을 따른다:
- 성공: {"ok": True, "data": ...}
- 실패: {"ok": False, "error": {"code": "<E_*>", "msg": str, "ctx": dict}}

주의: 주문은 기본 스텁(E_ORDER_STUB). M3에서 활성/고도화한다.
"""
from __future__ import annotations

import os
import time
import hmac
import json
import hashlib
import threading
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any, List

# -----------------------------------------------------------------------------
# HTTP drivers (httpx -> requests)
# -----------------------------------------------------------------------------
try:
    import httpx as _http
    _HTTP_HAVE = "httpx"
except Exception:  # pragma: no cover
    try:
        import requests as _http  # type: ignore
        _HTTP_HAVE = "requests"
    except Exception:
        _http = None
        _HTTP_HAVE = ""

# WS driver
try:
    from websocket import WebSocketApp  # websocket-client
    _WS_HAVE = "websocket-client"
except Exception:  # pragma: no cover
    WebSocketApp = None
    _WS_HAVE = ""


log = logging.getLogger("binance.client")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(code: str, msg: str, **ctx) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "msg": msg, "ctx": ctx}}


def _now_ms() -> int:
    return int(time.time() * 1000)


# ----------------------------------------------------------------------------- 
# [ANCHOR:BINANCE_CLIENT]
# -----------------------------------------------------------------------------
class BinanceClient:
    """
    USDⓈ-M Futures 전용 클라이언트. testnet↔live 토글 가능.

    생성은 보통 from_env()를 권장:
        cli = BinanceClient.from_env()

    주요 REST:
      - ping(), server_time(), exchange_info(), mark_price()
      - get_account(), get_positions()
      - create_order()  # 기본 스텁(E_ORDER_STUB)

    WS 구독:
      - subscribe_kline(symbol, interval, on_msg)
      - subscribe_mark_price(symbol, on_msg)
      - subscribe_user(listenKey, on_msg)

    각 subscribe_* 는 WSHandle을 반환하며, .stop() 으로 종료.
    """

    # 기본 엔드포인트 맵
    DEFAULTS = {
        "live": {
            "rest": "https://fapi.binance.com",
            "ws":   "wss://fstream.binance.com",
        },
        "testnet": {
            "rest": "https://testnet.binancefuture.com",
            "ws":   "wss://stream.binancefuture.com",
        },
    }

    def __init__(
        self,
        mode: str = "testnet",
        key: Optional[str] = None,
        secret: Optional[str] = None,
        *,
        rest_base: Optional[str] = None,
        ws_base: Optional[str] = None,
        recv_window_ms: int = 5000,
        order_active: bool = False,  # 기본 False → 스텁 반환
        http_timeout: float = 10.0,
    ) -> None:
        self.mode = (mode or "testnet").lower()
        if self.mode not in ("testnet", "live"):
            raise ValueError("mode must be 'testnet' or 'live'")

        defaults = self.DEFAULTS[self.mode]
        self.rest_base = (rest_base or os.getenv("REST_BASE_OVERRIDE") or defaults["rest"]).rstrip("/")
        self.ws_base = (ws_base or os.getenv("WS_BASE_OVERRIDE") or defaults["ws"]).rstrip("/")
        self.key = key or ""
        self.secret = secret or ""
        self.recv_window_ms = recv_window_ms
        self.order_active = order_active
        self.http_timeout = http_timeout

        log.info(
            "[BINANCE_CLIENT_STATUS] mode=%s rest=%s ws=%s http=%s ws_driver=%s",
            self.mode, self.rest_base, self.ws_base, _HTTP_HAVE or "none", _WS_HAVE or "none"
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, *, order_active: bool = False) -> "BinanceClient":
        """
        ENV 로드 규칙:
          MODE                 : testnet|live
          BINANCE_TESTNET_*    : 테스트넷 키
          BINANCE_LIVE_*       : 실계좌 키
          REST_BASE_OVERRIDE   : 선택 오버라이드
          WS_BASE_OVERRIDE     : 선택 오버라이드
        """
        mode = (os.getenv("MODE") or "testnet").lower()
        if mode == "live":
            key = os.getenv("BINANCE_LIVE_API_KEY") or ""
            secret = os.getenv("BINANCE_LIVE_API_SECRET") or ""
        else:
            key = os.getenv("BINANCE_TESTNET_API_KEY") or ""
            secret = os.getenv("BINANCE_TESTNET_API_SECRET") or ""

        cli = cls(mode, key, secret, order_active=order_active)
        # 부팅 요약
        log.info(
            "[BOOT_ENV_SUMMARY] MODE=%s, APIKEY=%s, REST_BASE=%s, WS_BASE=%s",
            mode, "EMPTY" if not key else "SET", cli.rest_base, cli.ws_base
        )
        if not key:
            log.warning("🔒 Binance API 키가 비어 있습니다. public 데이터만 동작하며 주문/계정 관련 기능은 비활성화됩니다.")
        return cli

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _http_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                      signed: bool = False, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        if _http is None:
            return _err("E_CONN_FAIL", "HTTP driver not available (install httpx or requests)")

        base = self.rest_base.rstrip("/")
        url = f"{base}{path}"
        params = dict(params or {})

        # futures REST는 /fapi/* 하위
        if not path.startswith("/fapi/"):
            url = f"{base}/fapi{path}"

        # 서명
        hdrs = dict(headers or {})
        if signed:
            if not self.key or not self.secret:
                return _err("E_KEY_EMPTY", "API key/secret required", path=path)
            hdrs["X-MBX-APIKEY"] = self.key
            params["timestamp"] = _now_ms()
            params["recvWindow"] = self.recv_window_ms
            query = "&".join(f"{k}={params[k]}" for k in sorted(params))
            sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            params["signature"] = sig
        else:
            # 일부 엔드포인트(유저스트림)는 KEY 헤더만 요구
            if "listenKey" in path or path.endswith("/listenKey"):
                if not self.key:
                    return _err("E_KEY_EMPTY", "API key required for user stream", path=path)
                hdrs["X-MBX-APIKEY"] = self.key

        try:
            if _HTTP_HAVE == "httpx":
                with _http.Client(timeout=self.http_timeout) as client:
                    if method == "GET":
                        r = client.get(url, params=params, headers=hdrs)
                    elif method == "POST":
                        r = client.post(url, params=params, headers=hdrs)
                    elif method == "PUT":
                        r = client.put(url, params=params, headers=hdrs)
                    elif method == "DELETE":
                        r = client.delete(url, params=params, headers=hdrs)
                    else:
                        return _err("E_HTTP_METHOD", f"Unsupported method {method}", path=path)
                    status = r.status_code
                    text = r.text
            else:
                if method == "GET":
                    r = _http.get(url, params=params, headers=hdrs, timeout=self.http_timeout)
                elif method == "POST":
                    r = _http.post(url, params=params, headers=hdrs, timeout=self.http_timeout)
                elif method == "PUT":
                    r = _http.put(url, params=params, headers=hdrs, timeout=self.http_timeout)
                elif method == "DELETE":
                    r = _http.delete(url, params=params, headers=hdrs, timeout=self.http_timeout)
                else:
                    return _err("E_HTTP_METHOD", f"Unsupported method {method}", path=path)
                status = r.status_code
                text = r.text

        except Exception as e:  # pragma: no cover
            return _err("E_CONN_FAIL", f"{e}", url=url, path=path)

        if status >= 400:
            # 429 → 레이트리밋 등도 여기로
            return _err("E_HTTP_STATUS", f"HTTP {status}", url=url, path=path, body=text)

        try:
            data = json.loads(text) if text else {}
        except Exception as e:
            return _err("E_DECODE", f"json decode fail: {e}", body=text[:200])

        return _ok(data)

    # ------------------------------------------------------------------
    # REST: public
    # ------------------------------------------------------------------
    def ping(self) -> Dict[str, Any]:
        r = self._http_request("GET", "/v1/ping")
        if not r["ok"]:
            return r
        # /ping 은 빈 바디 → latency 측정용으로 time API 한 번 더
        t0 = time.perf_counter()
        _ = self.server_time()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return _ok({"latency_ms": latency_ms})


    def server_time(self) -> Dict[str, Any]:
        return self._http_request("GET", "/v1/time")

    def exchange_info(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if symbols:
            # ["BTCUSDT","ETHUSDT"] → '["BTCUSDT","ETHUSDT"]'
            params["symbols"] = json.dumps(symbols)
        return self._http_request("GET", "/v1/exchangeInfo", params=params)

    def mark_price(self, symbol: str) -> Dict[str, Any]:
        r = self._http_request("GET", "/v1/premiumIndex", params={"symbol": symbol})
        if not r["ok"]:
            return r
        d = r["data"]
        # d = {..., "markPrice": "xxxxx.x", "time": 123}
        try:
            d["markPrice"] = float(d.get("markPrice"))
        except Exception:
            pass
        return _ok(d)

    # ------------------------------------------------------------------
    # REST: signed
    # ------------------------------------------------------------------
    def get_account(self) -> Dict[str, Any]:
        # futures 계정 정보
        return self._http_request("GET", "/v2/account", signed=True)

    def get_positions(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if symbols and len(symbols) == 1:
            params["symbol"] = symbols[0]
        # v2/positionRisk 는 심볼 미지정 시 전체 반환
        return self._http_request("GET", "/v2/positionRisk", params=params, signed=True)

    def create_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        주문 스텁: 기본은 실제 전송하지 않고 E_ORDER_STUB 반환.
        M3에서 order_active(True) 또는 ENV로 활성화 예정.

        기대 payload 예:
          {"symbol":"BTCUSDT","side":"BUY","type":"MARKET","quantity":"0.001","newClientOrderId":"..."}
        """
        if not self.order_active:
            return _err("E_ORDER_STUB", "order endpoint is stubbed (disabled)", payload=payload)
        return self._http_request("POST", "/v1/order", params=payload, signed=True)

    # ------------------------------------------------------------------
    # User Data Stream (listenKey)
    # ------------------------------------------------------------------
    def start_user_stream(self) -> Dict[str, Any]:
        return self._http_request("POST", "/v1/listenKey")  # header: X-MBX-APIKEY

    def keepalive_user_stream(self, listen_key: str) -> Dict[str, Any]:
        return self._http_request("PUT", "/v1/listenKey", params={"listenKey": listen_key})

    def close_user_stream(self, listen_key: str) -> Dict[str, Any]:
        return self._http_request("DELETE", "/v1/listenKey", params={"listenKey": listen_key})

    # ------------------------------------------------------------------
    # WS subscribe (단일 스트림 방식 /ws/<streamName>)
    # ------------------------------------------------------------------
    def subscribe_kline(self, symbol: str, interval: str, on_msg: Callable[[Dict[str, Any]], None]) -> "WSHandle":
        stream = f"{symbol.lower()}@kline_{interval}"
        url = f"{self.ws_base}/ws/{stream}"
        return self._start_ws(url, on_msg)

    def subscribe_mark_price(self, symbol: str, on_msg: Callable[[Dict[str, Any]], None]) -> "WSHandle":
        # 1초 마다 마크프라이스
        stream = f"{symbol.lower()}@markPrice@1s"
        url = f"{self.ws_base}/ws/{stream}"
        return self._start_ws(url, on_msg)

    def subscribe_user(self, listen_key: str, on_msg: Callable[[Dict[str, Any]], None]) -> "WSHandle":
        url = f"{self.ws_base}/ws/{listen_key}"
        return self._start_ws(url, on_msg)

    # ------------------------------------------------------------------
    # WS internals
    # ------------------------------------------------------------------
    def _start_ws(self, url: str, on_msg: Callable[[Dict[str, Any]], None]) -> "WSHandle":
        if not _WS_HAVE:
            # 폴백: 드물지만 WS 드라이버 없으면 에러. (원하면 REST 폴링으로 대체)
            log.error("websocket-client 미설치. pip install websocket-client")
            return WSHandle(error=_err("E_WS_DRIVER_MISSING", "websocket-client not installed", url=url))

        stop_event = threading.Event()

        def _run():
            backoff = 1.0
            max_back = 30.0

            def _on_open(_ws):
                log.info("[WS OPEN] %s", url)

            def _on_message(_ws, message):
                try:
                    data = json.loads(message)
                except Exception:
                    log.warning("[WS DECODE FAIL] %s ...", message[:120])
                    return
                try:
                    on_msg(data)
                except Exception as e:
                    log.exception("on_msg error: %s", e)

            def _on_error(_ws, error):
                log.warning("[WS ERROR] %s %s", url, error)

            def _on_close(_ws, status_code, msg):
                log.info("[WS CLOSE] %s status=%s msg=%s", url, status_code, msg)

            while not stop_event.is_set():
                try:
                    app = WebSocketApp(
                        url,
                        on_open=_on_open,
                        on_message=_on_message,
                        on_error=_on_error,
                        on_close=_on_close,
                    )
                    app.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:  # pragma: no cover
                    log.warning("[WS EXCEPT] %s %s", url, e)

                if stop_event.is_set():
                    break

                # 재연결 백오프
                log.info("[WS RECONNECT] %s in %.1fs", url, backoff)
                time.sleep(backoff)
                backoff = min(max_back, backoff * 2)

        th = threading.Thread(target=_run, name=f"ws:{url}", daemon=True)
        th.start()
        return WSHandle(url=url, stop_event=stop_event, thread=th)


@dataclass
class WSHandle:
    url: str = ""
    stop_event: Optional[threading.Event] = None
    thread: Optional[threading.Thread] = None
    error: Optional[Dict[str, Any]] = None

    def stop(self, timeout: float = 2.0) -> None:
        if self.stop_event:
            self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)
        logging.getLogger("binance.client").info("[WS STOP] %s", self.url)


