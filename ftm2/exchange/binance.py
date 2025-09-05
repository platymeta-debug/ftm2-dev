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
import concurrent.futures
from urllib.parse import urlencode
from dataclasses import dataclass
from typing import Callable, Optional, Dict, Any, List

from .http_driver import HttpDriver

try:
    from ftm2.core.env import (
        load_env_chain,
        load_binance_credentials,
    )
except Exception:  # pragma: no cover
    from core.env import load_env_chain  # type: ignore
    from core.env import load_binance_credentials  # type: ignore

LIVE_MARK_WS = "wss://fstream.binance.com/ws"  # 시장 데이터는 항상 라이브

_http_drv = HttpDriver()


def boot_http() -> None:
    """Ensure HTTP driver is started."""
    if _http_drv._mode is None:
        _http_drv.start()


def get_klines(symbol: str, interval: str, limit: int = 600):
    """Simple REST helper for warmup. Returns raw kline rows."""
    boot_http()
    url = "https://fapi.binance.com/fapi/v1/klines"
    return _http_drv.get(url, params={"symbol": symbol, "interval": interval, "limit": limit})



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

_BINANCE_ERROR_HINTS = {
    -2015: "API 키 또는 권한 오류",
    -2019: "잔고 부족",
    -2022: "reduceOnly 위반 또는 포지션 없음",
    -1111: "값 범위 또는 소수 자릿수 오류",
    -1022: "시그니처 검증 실패",
}


# [ANCHOR:WS_MANAGER] begin
"""
웹소켓 종료를 병렬로 처리하여 전체 셧다운 시간을 줄인다.
- ws_open() 시 register()로 등록
- stop_all_parallel()로 일괄 종료(타임아웃/로그 포함)
ENV:
  WS_STOP_PARALLEL=1        # 1=병렬, 0=직렬
  WS_STOP_TIMEOUT_S=3       # 각 WS join 타임아웃
  WS_STOP_MAX_WORKERS=8     # 병렬 종료 스레드 수 한도
"""
class _WSHandle:
    __slots__ = ("url", "closer", "thread")

    def __init__(self, url: str, closer, thread: threading.Thread | None):
        self.url = url
        self.closer = closer             # callable: () -> None
        self.thread = thread             # ws run_forever thread

_WS_REG: dict[str, _WSHandle] = {}
_WS_LOCK = threading.Lock()


def ws_register(url: str, closer, thread: threading.Thread | None):
    """WS 생성 직후 호출해서 레지스트리에 추가."""
    h = _WSHandle(url, closer, thread)
    with _WS_LOCK:
        _WS_REG[url] = h
    log.info("[WS OPEN] %s", url)


def ws_unregister(url: str):
    with _WS_LOCK:
        _WS_REG.pop(url, None)


def _close_one(h: _WSHandle, timeout_s: float):
    try:
        # idempotent closer (여러 번 불러도 안전)
        h.closer()
    except Exception:
        log.exception("E_WS_CLOSE url=%s", h.url)
    if h.thread:
        h.thread.join(timeout=timeout_s)
        if h.thread.is_alive():
            log.warning("E_WS_STOP_TIMEOUT url=%s timeout=%.1fs", h.url, timeout_s)
        else:
            log.info("[WS STOP] %s", h.url)
    else:
        log.info("[WS STOP] %s (no-thread)", h.url)


def ws_stop_all_parallel():
    """등록된 모든 WS를 (옵션) 병렬로 종료한다."""
    with _WS_LOCK:
        handles = list(_WS_REG.values())
        _WS_REG.clear()

    if not handles:
        log.info("[WS STOPPED] none")
        return

    timeout_s = float(os.getenv("WS_STOP_TIMEOUT_S", "3").strip() or "3")
    parallel = os.getenv("WS_STOP_PARALLEL", "1").strip() in ("1", "true", "True", "YES", "yes")
    max_workers = int(os.getenv("WS_STOP_MAX_WORKERS", "8").strip() or "8")
    max_workers = max(1, min(max_workers, len(handles)))

    t0 = time.time()
    if parallel and len(handles) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_close_one, h, timeout_s) for h in handles]
            concurrent.futures.wait(futs, timeout=timeout_s + 2)
    else:
        for h in handles:
            _close_one(h, timeout_s)

    log.info("[WS STOPPED] all streams closed in %.2fs (n=%d, parallel=%s)",
             time.time() - t0, len(handles), parallel)
# [ANCHOR:WS_MANAGER] end


def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(code: str, msg: str, **ctx) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "msg": msg, "ctx": ctx}}


def _now_ms() -> int:
    return int(time.time() * 1000)


# -----------------------------------------------------------------------------
# [ANCHOR:BINANCE_CLIENT] begin
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

    # --- helpers: unified key loader & env auto-detect ------------------
    @staticmethod
    def _load_keypair_unified(prefer: str | None = None) -> tuple[str, str]:
        """
        우선순위:
          1) 공통: BINANCE_API_KEY / BINANCE_API_SECRET
             (또는 BINANCE_KEY / BINANCE_SECRET, TOKEN / TOKEN_SECRET 호환)
          2) 선호(prefer)가 지정되면 해당 환경 키를 가산:
             prefer == "live"    -> BINANCE_LIVE_API_KEY / _SECRET
             prefer == "testnet" -> BINANCE_TESTNET_API_KEY / _SECRET
          3) 마지막 백업으로 서로 다른 환경 변수 중 먼저 발견되는 값 사용
        """
        import os
        key = (
            os.getenv("BINANCE_API_KEY")
            or os.getenv("BINANCE_KEY")
            or os.getenv("TOKEN")
            or ""
        )
        secret = (
            os.getenv("BINANCE_API_SECRET")
            or os.getenv("BINANCE_SECRET")
            or os.getenv("TOKEN_SECRET")
            or ""
        )
        # prefer 가 지정되면 그쪽 ENV로 보강
        if prefer == "live":
            key = os.getenv("BINANCE_LIVE_API_KEY") or key
            secret = os.getenv("BINANCE_LIVE_API_SECRET") or secret
        elif prefer == "testnet":
            key = os.getenv("BINANCE_TESTNET_API_KEY") or key
            secret = os.getenv("BINANCE_TESTNET_API_SECRET") or secret
        else:
            # 아무것도 없을 때 두 환경 것을 순서대로 시도
            key = (
                os.getenv("BINANCE_API_KEY")
                or os.getenv("BINANCE_LIVE_API_KEY")
                or os.getenv("BINANCE_TESTNET_API_KEY")
                or key
            )
            secret = (
                os.getenv("BINANCE_API_SECRET")
                or os.getenv("BINANCE_LIVE_API_SECRET")
                or os.getenv("BINANCE_TESTNET_API_SECRET")
                or secret
            )
        return key or "", secret or ""

    @staticmethod
    def _detect_trade_env(api_key: str, timeout: float = 2.5) -> str:
        """
        주어진 API KEY가 어느 환경의 키인지 자동 판별.
        - futures listenKey 생성(서명 불필요)을 testnet→live 순으로 시도
        - 200이면 해당 환경
        - 둘 다 실패하면 안전하게 'testnet'
        """
        if not api_key:
            return "testnet"
        headers = {"X-MBX-APIKEY": api_key}
        post = None
        try:
            import httpx  # type: ignore
            post = lambda url: httpx.post(url, headers=headers, timeout=timeout)
        except Exception:
            try:
                import requests  # type: ignore
                post = lambda url: requests.post(url, headers=headers, timeout=timeout)
            except Exception:
                return "testnet"
        try:
            r = post("https://testnet.binancefuture.com/fapi/v1/listenKey")
            if getattr(r, "status_code", 0) == 200:
                return "testnet"
        except Exception:
            pass
        try:
            r = post("https://fapi.binance.com/fapi/v1/listenKey")
            if getattr(r, "status_code", 0) == 200:
                return "live"
        except Exception:
            pass
        return "testnet"

    def __init__(
        self,
        mode: str = "testnet",
        key: Optional[str] = None,
        secret: Optional[str] = None,
        *,
        rest_base: Optional[str] = None,
        ws_base: Optional[str] = None,
        recv_window_ms: int = 30000,
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
        self.recv_window_ms = int(os.getenv("BINANCE_RECV_WINDOW", str(recv_window_ms)))
        self._clock_offset_ms = 0
        self.order_active = order_active
        self.http_timeout = http_timeout

        self.http: str | None = None
        self._bind_http_driver()

        log.info(
            "[BINANCE_CLIENT_STATUS] mode=%s rest=%s ws=%s http=%s ws_driver=%s",
            self.mode,
            self.rest_base,
            self.ws_base,
            self.http or "none",
            _WS_HAVE or "none",
        )

    def sync_time(self) -> None:
        t0 = int(time.time() * 1000)
        r = self._http_request("GET", "/v1/time")
        t1 = int(time.time() * 1000)
        if not r.get("ok"):
            raise RuntimeError("TIME_SYNC_FAIL")
        server_ms = (r.get("data") or {}).get("serverTime")
        if isinstance(server_ms, (int, float)):
            self._clock_offset_ms = int(server_ms - (t0 + t1) // 2)

    def _now_ms(self) -> int:
        return int(time.time() * 1000 + self._clock_offset_ms)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        *,
        for_trade: bool = True,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> "BinanceClient":
        load_env_chain()
        mode = (os.getenv("MODE") or "testnet").lower()
        if mode not in ("testnet", "live"):
            mode = "testnet"
        key = (
            api_key
            or os.getenv("BINANCE_API_KEY")
            or os.getenv(f"BINANCE_{mode.upper()}_API_KEY")
        )
        secret = (
            api_secret
            or os.getenv("BINANCE_API_SECRET")
            or os.getenv(f"BINANCE_{mode.upper()}_API_SECRET")
        )
        if for_trade and (not key or not secret):
            raise RuntimeError("BINANCE API key/secret not found in env")
        cli = cls(mode, key or "", secret or "", order_active=for_trade)
        if for_trade:
            try:
                cli.sync_time()
            except Exception as e:  # pragma: no cover
                log.warning("TIME_SYNC_FAIL: %s", e)
        return cli

    # [ANCHOR:DUAL_MODE]
    @classmethod
    def for_data(cls, mode: str = "live") -> "BinanceClient":
        """
        공개 시세/클라인 전용 클라이언트. API 키 불필요.
        mode: live | testnet | replay(=testnet)
        """
        return cls(
            mode=("testnet" if mode == "testnet" else "live"),
            order_active=False,
        )

    @classmethod
    def for_trade(cls, mode: str, order_active: bool = True) -> "BinanceClient":
        """
        mode: 'auto' | 'live' | 'testnet' | 'dry'
        - 'auto' : load_binance_credentials() 로 자동 환경 감지
        - 그 외  : 해당 환경으로 강제 지정
        """
        m = (mode or "auto").lower()
        if m == "dry":
            cli = cls("testnet", "", "", order_active=False)
        else:
            creds = load_binance_credentials()
            if m == "auto":
                env = creds.env
            elif m == "live":
                env = "live"
            elif m == "testnet":
                env = "testnet"
            else:
                env = "testnet"
            cli = cls(env, creds.api_key, creds.api_secret, order_active=order_active)
        try:
            cli.sync_time()
        except Exception as e:  # pragma: no cover
            log.warning("TIME_SYNC_FAIL: %s", e)
        return cli

    # ------------------------------------------------------------------
    # HTTP driver binding
    # ------------------------------------------------------------------
    def _bind_http_driver(self) -> None:
        if self.http:
            return
        try:
            import httpx  # noqa: F401
            self.http = "httpx"
        except Exception:  # pragma: no cover
            try:
                import requests  # noqa: F401
                self.http = "requests"
            except Exception:
                self.http = None
        logging.getLogger("binance.client").info(
            "[HTTP DRIVER] selected=%s", self.http or "none"
        )

    def ensure_http(self) -> None:
        if not self.http:
            self._bind_http_driver()
        if not self.http:
            raise RuntimeError("HTTP driver not available")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _encode_and_sign(self, params: Dict[str, Any]) -> str:
        """URL-encode params and append HMAC-SHA256 signature."""
        params = dict(params)
        params.setdefault("recvWindow", self.recv_window_ms)
        if "timestamp" not in params:
            params["timestamp"] = self._now_ms()
        encoded = urlencode(params, doseq=True)
        sig = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}&signature={sig}"

    def _http_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                      signed: bool = False, headers: Optional[Dict[str, str]] = None,
                      _retried: bool = False) -> Dict[str, Any]:
        if self.http not in ("httpx", "requests"):
            return _err("E_CONN_FAIL", "HTTP driver not available (install httpx or requests)")

        base = self.rest_base.rstrip("/")
        url = f"{base}{path}"
        params = dict(params or {})
        orig_params = dict(params)

        # futures REST는 /fapi/* 하위
        if not path.startswith("/fapi/"):
            url = f"{base}/fapi{path}"

        # 서명
        hdrs = dict(headers or {})
        orig_hdrs = dict(hdrs)
        payload: Optional[str] = None
        if signed:
            if not self.key or not self.secret:
                return _err("E_KEY_EMPTY", "API key/secret required", path=path)
            hdrs["X-MBX-APIKEY"] = self.key
            payload = self._encode_and_sign(params)
        else:
            # 일부 엔드포인트(유저스트림)는 KEY 헤더만 요구
            if "listenKey" in path or path.endswith("/listenKey"):
                if not self.key:
                    return _err("E_KEY_EMPTY", "API key required for user stream", path=path)
                hdrs["X-MBX-APIKEY"] = self.key

        try:
            if self.http == "httpx":
                import httpx
                with httpx.Client(timeout=self.http_timeout) as client:
                    if signed:
                        if method in ("GET", "DELETE"):
                            r = client.request(method, f"{url}?{payload}", headers=hdrs)
                        else:  # POST, PUT
                            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
                            r = client.request(method, url, content=payload, headers=hdrs)
                    else:
                        r = client.request(method, url, params=params, headers=hdrs)
                    status = r.status_code
                    text = r.text
            else:
                import requests
                if signed:
                    if method in ("GET", "DELETE"):
                        r = requests.request(method, f"{url}?{payload}", headers=hdrs, timeout=self.http_timeout)
                    else:
                        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
                        r = requests.request(method, url, data=payload, headers=hdrs, timeout=self.http_timeout)
                else:
                    r = requests.request(method, url, params=params, headers=hdrs, timeout=self.http_timeout)
                status = r.status_code
                text = r.text

        except Exception as e:  # pragma: no cover
            return _err("E_CONN_FAIL", f"{e}", url=url, path=path)

        if status >= 400:
            err: Dict[str, Any] = {}
            try:
                err = json.loads(text) if text else {}
            except Exception:
                err = {"msg": text}
            code = err.get("code")
            msg = err.get("msg")
            ts_err = (code == -1021) or ("Timestamp" in str(msg))
            if signed and ts_err and not _retried:
                try:
                    self.sync_time()
                except Exception as e:  # pragma: no cover
                    log.warning("TIME_SYNC_FAIL: %s", e)
                return self._http_request(
                    method,
                    path,
                    params=orig_params,
                    signed=signed,
                    headers=orig_hdrs,
                    _retried=True,
                )
            log.warning(
                "[BINANCE_HTTP_ERR] %s %s code=%s msg=%s", method, path, code, msg
            )
            return _err(
                "E_HTTP_STATUS",
                f"HTTP {status}",
                url=url,
                path=path,
                body=text,
                binance_code=code,
                binance_msg=msg,
            )

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

    def klines(self, symbol: str, interval: str, limit: int = 500) -> Dict[str, Any]:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return self._http_request("GET", "/v1/klines", params=params)

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

    def get_balance_usdt(self) -> Dict[str, float]:
        if not self.key or not self.secret:
            raise RuntimeError("NO_API_KEY")
        r = self._http_request("GET", "/v2/balance", signed=True)
        if not r.get("ok"):
            err = r.get("error", {})
            raise RuntimeError(err.get("code", "E_BAL"))
        for b in r.get("data", []):
            if b.get("asset") == "USDT":
                wb = float(b.get("balance") or b.get("wb") or 0.0)
                cw = float(b.get("crossWalletBalance") or b.get("cw") or 0.0)
                return {"wallet": wb, "avail": cw}
        raise RuntimeError("USDT_NOT_FOUND")

    # [ANCHOR:BINANCE_CLIENT_BAL]
    def get_equity(self) -> Optional[float]:
        if not self.key or not self.secret:
            return None
        r = self._http_request("GET", "/v2/balance", signed=True)
        if r.get("ok"):
            data = r.get("data") or []
            usdt = next((x for x in data if x.get("asset") == "USDT"), None)
            if usdt:
                try:
                    return float(usdt.get("balance") or usdt.get("crossWalletBalance") or 0.0)
                except Exception:
                    pass
        alt = self._http_request("GET", "/v2/account", signed=True)
        if not alt.get("ok"):
            return None
        d = alt.get("data") or {}
        try:
            return float(d.get("totalMarginBalance") or d.get("totalWalletBalance") or 0.0)
        except Exception:
            return None

    # --- 신규: 통합 잔고/에쿼티 조회 -----------------------------------
    def fetch_account_equity(self) -> Dict[str, float]:
        """/fapi/v2/account 기반의 잔고 정보를 반환한다."""
        r = self._http_request("GET", "/v2/account", signed=True)
        if not r.get("ok"):
            return {}
        d = r.get("data") or {}
        try:
            wallet = float(d.get("totalWalletBalance") or 0.0)
            upnl = float(d.get("totalUnrealizedProfit") or 0.0)
            equity = float(d.get("totalMarginBalance") or (wallet + upnl))
            avail = 0.0
            for a in d.get("assets", []):
                if (a.get("asset") or "").upper() == "USDT":
                    try:
                        avail = float(a.get("availableBalance") or 0.0)
                    except Exception:
                        avail = 0.0
                    break
            return {"wallet": wallet, "equity": equity, "upnl": upnl, "avail": avail}
        except Exception:
            return {}

    # 간편 equity 조회
    def equity(self) -> float:
        eq = self.fetch_account_equity()
        try:
            return float(eq.get("equity", 0.0))
        except Exception:
            return 0.0

    # --- 신규: 포지션 조회 ---------------------------------------------
    def fetch_positions(self, symbols: List[str] | None = None) -> Dict[str, Dict[str, Any]]:
        """/fapi/v2/account 의 positions 필드를 사용해 현재 포지션을 조회한다."""
        r = self._http_request("GET", "/v2/account", signed=True)
        if not r.get("ok"):
            return {}
        data = r.get("data") or {}
        out: Dict[str, Dict[str, Any]] = {}
        pos_list = data.get("positions") or []
        for p in pos_list:
            sym = (p.get("symbol") or "").upper()
            if not sym:
                continue
            if symbols and sym not in symbols:
                continue
            try:
                qty = float(p.get("positionAmt") or 0.0)
            except Exception:
                qty = 0.0
            if abs(qty) == 0:
                continue
            try:
                ep = float(p.get("entryPrice") or 0.0)
                up = float(p.get("unrealizedProfit")) if "unrealizedProfit" in p else float(p.get("unRealizedProfit", 0.0))
                lev = float(p.get("leverage") or 0.0)
            except Exception:
                ep = up = lev = 0.0
            out[sym] = {
                "symbol": sym,
                "pa": qty,
                "ep": ep,
                "up": up,
                "leverage": lev,
                "marginType": p.get("marginType"),
            }
        return out

    def create_order(self, payload: Dict[str, Any], *, validate_only: bool = False) -> Dict[str, Any]:
        """
        주문 스텁: 기본은 실제 전송하지 않고 E_ORDER_STUB 반환.
        M3에서 order_active(True) 또는 ENV로 활성화 예정.

        기대 payload 예:
          {"symbol":"BTCUSDT","side":"BUY","type":"MARKET","quantity":"0.001","newClientOrderId":"..."}
        """
        path = "/v1/order/test" if validate_only else "/v1/order"
        if not self.order_active and not validate_only:
            return _err("E_ORDER_STUB", "order endpoint is stubbed (disabled)", payload=payload)
        data = dict(payload)
        if (data.get("type") or "").upper() == "MARKET":
            data.pop("timeInForce", None)
            data.pop("price", None)
        if "reduceOnly" in data:
            data["reduceOnly"] = "true" if data["reduceOnly"] else "false"
        r = self._http_request("POST", path, params=data, signed=True)
        if not r.get("ok"):
            err = r.get("error") or {}
            ctx = err.get("ctx") or {}
            code = ctx.get("binance_code") or ctx.get("code")
            msg = ctx.get("binance_msg") or ctx.get("msg")
            reason = None
            try:
                reason = _BINANCE_ERROR_HINTS.get(int(code))
            except Exception:
                reason = None
            if reason:
                log.warning("[ORDER_FAIL] code=%s msg=%s (%s)", code, msg, reason)
            else:
                log.warning("[ORDER_FAIL] code=%s msg=%s", code, msg)
        return r
# [ANCHOR:BINANCE_CLIENT] end

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
        app_ref: Dict[str, Any] = {"app": None}

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
                    app_ref["app"] = app
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

        def _closer():
            stop_event.set()
            app = app_ref.get("app")
            if app is not None:
                try:
                    app.close()
                except Exception:
                    pass

        try:
            ws_register(url, closer=_closer, thread=th)
        except Exception:
            pass

        return WSHandle(url=url, stop_event=stop_event, thread=th, closer=_closer)


@dataclass
class WSHandle:
    url: str = ""
    stop_event: Optional[threading.Event] = None
    thread: Optional[threading.Thread] = None
    closer: Optional[Callable[[], None]] = None
    error: Optional[Dict[str, Any]] = None

    def stop(self, timeout: float = 2.0) -> None:
        if self.closer:
            try:
                self.closer()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                logging.getLogger("binance.client").warning(
                    "E_WS_STOP_TIMEOUT url=%s timeout=%.1fs", self.url, timeout
                )
        ws_unregister(self.url)
        logging.getLogger("binance.client").info("[WS STOP] %s", self.url)


