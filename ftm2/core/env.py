# -*- coding: utf-8 -*-
"""
간단 ENV 체인 로더
- 우선순위: os.environ > token.env > .env
- 파일이 없으면 무시. 값은 'KEY=VALUE' 형태만 인식.
"""
from __future__ import annotations
import os
import time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional

import httpx

# [ANCHOR:ENV_LOADER]
def _parse_env_file(path: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                kv[k] = v
    except FileNotFoundError:
        pass
    return kv

def load_env_chain(paths: Tuple[str, ...] = ("token.env", ".env")) -> Dict[str, str]:
    # 1) 시작은 현재 OS 환경을 복사
    env: Dict[str, str] = dict(os.environ)

    # 2) token.env → .env 순서로, **존재하지 않는 키만** 주입
    for p in paths:
        kv = _parse_env_file(p)
        for k, v in kv.items():
            if k not in env or env.get(k) in (None, ""):
                os.environ.setdefault(k, v)
                env.setdefault(k, v)

    return env


# ---- Binance unified credential loader -----------------------------------
# REST / WS base endpoints
BINANCE_LIVE_REST = "https://fapi.binance.com"
BINANCE_TEST_REST = "https://testnet.binancefuture.com"
BINANCE_LIVE_WS_USTREAM = "wss://fstream.binance.com/ws"
BINANCE_TEST_WS_USTREAM = "wss://stream.binancefuture.com/ws"

_ENV_CACHE: Optional[Tuple[str, float]] = None  # (env, ts)


def _first(*names: str) -> Optional[str]:
    for n in names:
        v = os.getenv(n) or os.getenv(n.lower())
        if v:
            return v.strip()
    return None


@dataclass
class BinanceCreds:
    api_key: str
    api_secret: str
    env: str                 # "live" or "testnet"
    rest_base: str
    ustream_ws: str          # userDataStream ws base


def detect_binance_env(api_key: str, api_secret: str) -> str:
    """Detect live/testnet automatically by pinging the REST endpoint."""
    global _ENV_CACHE
    now = time.time()
    if _ENV_CACHE and now - _ENV_CACHE[1] < 3600:
        return _ENV_CACHE[0]

    def _ok(base: str) -> bool:
        try:
            with httpx.Client(timeout=3.0) as c:
                r = c.get(f"{base}/fapi/v1/ping")
                return r.status_code == 200
        except Exception:
            return False

    env = "live" if _ok(BINANCE_LIVE_REST) else "testnet"
    _ENV_CACHE = (env, now)
    return env


def load_binance_credentials() -> BinanceCreds:
    """Load API credentials and determine environment.

    Order of precedence:
    1) BINANCE_ENV or USE_TESTNET to force selection
    2) automatic detection via REST ping
    Fallback key names: BINANCE_API_KEY|BINANCE_KEY|FUTURES_API_KEY|TOKEN
    and BINANCE_API_SECRET|BINANCE_SECRET|FUTURES_API_SECRET
    """

    api_key = _first("BINANCE_API_KEY", "BINANCE_KEY", "FUTURES_API_KEY", "TOKEN")
    api_secret = _first("BINANCE_API_SECRET", "BINANCE_SECRET", "FUTURES_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError(
            "BINANCE API key/secret not found in env (checked BINANCE_API_KEY/SECRET, TOKEN, ...)."
        )

    env_hint = _first("BINANCE_ENV")
    use_testnet = _first("USE_TESTNET")
    if env_hint:
        env = "testnet" if env_hint.lower().startswith("test") else "live"
    elif use_testnet:
        env = "testnet" if use_testnet.lower() in ("1", "true", "yes", "y") else "live"
    else:
        env = detect_binance_env(api_key, api_secret)

    if env == "testnet":
        return BinanceCreds(api_key, api_secret, env, BINANCE_TEST_REST, BINANCE_TEST_WS_USTREAM)
    return BinanceCreds(api_key, api_secret, env, BINANCE_LIVE_REST, BINANCE_LIVE_WS_USTREAM)

