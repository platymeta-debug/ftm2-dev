# -*- coding: utf-8 -*-
"""
FTM2 Orchestrator (minimal)
- ENV 로딩 → BinanceClient 생성 → WS 스트림(StreamManager) 시작
- 10초 하트비트 로그
- Ctrl+C 안전 종료
"""
from __future__ import annotations

import os
import time
import signal
import logging
import threading
from typing import List

# 로컬 모듈
try:
    from ftm2.core.env import load_env_chain
    from ftm2.core.state import StateBus
    from ftm2.data.streams import StreamManager
except Exception:  # pragma: no cover
    from core.env import load_env_chain  # type: ignore
    from core.state import StateBus  # type: ignore
    from data.streams import StreamManager  # type: ignore

try:
    from ftm2.exchange.binance import BinanceClient
except Exception:  # pragma: no cover
    from exchange.binance import BinanceClient  # type: ignore

try:
    from ftm2.core.persistence import Persistence
except Exception:  # pragma: no cover
    from core.persistence import Persistence  # type: ignore

try:
    from ftm2.discord_bot.bot import run_discord_bot
except Exception:  # pragma: no cover
    from discord_bot.bot import run_discord_bot  # type: ignore

log = logging.getLogger("ftm2.orch")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# [ANCHOR:ORCH]
class Orchestrator:
    def __init__(self) -> None:
        self.env = load_env_chain()
        self.mode = (os.getenv("MODE") or "testnet").lower()
        self.symbols: List[str] = [s.strip() for s in (os.getenv("SYMBOLS") or "BTCUSDT,ETHUSDT").split(",") if s.strip()]
        self.tf_exec = os.getenv("TF_EXEC") or "1m"
        self.kline_intervals = [s.strip() for s in (os.getenv("TF_SIGNAL") or "5m,15m,1h,4h").split(",") if s.strip()]

        self.bus = StateBus()
        self.cli = BinanceClient.from_env(order_active=False)
        self.streams = StreamManager(self.cli, self.bus, self.symbols, self.kline_intervals, use_mark=True, use_user=True)

        self.db_path = os.getenv("DB_PATH") or "./runtime/trader.db"
        self.db = Persistence(self.db_path)
        self.db.ensure_schema()
        try:
            self.db.record_event("INFO", "system", "boot")
        except Exception:
            pass

        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

        # 부팅 요약
        log.info("[BOOT_ENV_SUMMARY] MODE=%s, SYMBOLS=%s, TF_EXEC=%s, TF_SIGNAL=%s", self.mode, self.symbols, self.tf_exec, self.kline_intervals)
        for k in ("DB_PATH", "CONFIG_PATH", "PATCH_LOG"):
            if os.getenv(k):
                log.info("[BOOT_PATH] %s=%s", k, os.getenv(k))

    # -------- optional: simple REST poller (mark price) ----------
    def _price_poller(self, symbol: str, interval_s: float = 3.0) -> None:
        while not self._stop.is_set():
            r = self.cli.mark_price(symbol)
            if r.get("ok"):
                d = r["data"]
                price = float(d.get("markPrice", 0.0))
                ts = int(d.get("time") or int(time.time() * 1000))
                self.bus.update_mark(symbol, price, ts)
                log.debug("[PRICE_POLL] %s price=%s ts=%s", symbol, price, ts)
            time.sleep(interval_s)

    def start(self) -> None:
        # 심볼별 마크프라이스 폴러는 M1.1 임시 → WS로 대체
        # for sym in self.symbols:
        #     t = threading.Thread(target=self._price_poller, args=(sym,), name=f"poll:{sym}", daemon=True)
        #     t.start()
        #     self._threads.append(t)

        # WS 스트림 시작
        self.streams.start()

        # 하트비트 스레드
        t = threading.Thread(target=self._heartbeat, name="heartbeat", daemon=True)
        t.start()
        self._threads.append(t)

        # Discord 봇 (토큰 없으면 내부에서 자동 비활성 로그 후 종료)
        dt = threading.Thread(target=run_discord_bot, args=(self.bus,), name="discord-bot", daemon=True)
        dt.start()
        self._threads.append(dt)

        # 시그널 핸들
        try:
            signal.signal(signal.SIGINT, self._signal_stop)
            signal.signal(signal.SIGTERM, self._signal_stop)
        except Exception:
            pass

    def _heartbeat(self, period_s: int = 10) -> None:
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            marks = snap["marks"]
            lines = []
            for s in self.symbols:
                if s in marks:
                    lines.append(f"{s}={marks[s]['price']}")
            uptime = self.bus.uptime_s()
            log.info("[HEARTBEAT] mode=%s uptime=%ss symbols=%d marks={%s}", self.mode, uptime, len(marks), ", ".join(lines))
            time.sleep(period_s)

    def _signal_stop(self, *_):
        log.info("[SHUTDOWN] stop requested")
        self.stop()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self.streams.stop()
        except Exception:
            pass
        for t in list(self._threads):
            if t.is_alive():
                t.join(timeout=2.0)
        try:
            self.db.record_event("INFO", "system", "shutdown")
            self.db.close()
        except Exception:
            pass
        log.info("[SHUTDOWN] orchestrator stopped")


def run() -> None:
    orch = Orchestrator()
    orch.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        orch.stop()


if __name__ == "__main__":
    run()
