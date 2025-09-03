# -*- coding: utf-8 -*-
"""
Replay Engine: ndjson/CSV 캡처 재생 → StateBus 주입
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import os, json, csv, time, threading, logging

try:
    from ftm2.core.state import StateBus
except Exception:
    from core.state import StateBus  # type: ignore

log = logging.getLogger("ftm2.replay")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@dataclass
class ReplayConfig:
    enabled: bool = False
    src: str = "./data/replay.ndjson"
    speed: float = 5.0         # >1이면 가속
    loop: bool = False
    default_interval: str = "1m"

# [ANCHOR:REPLAY_ENGINE]
class ReplayEngine:
    def __init__(self, bus: StateBus, db: Optional[Any], cfg: ReplayConfig = ReplayConfig()) -> None:
        self.bus = bus
        self.db = db
        self.cfg = cfg
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None

    # ---- loaders ----
    def _load_ndjson(self, path: str) -> List[Dict[str, Any]]:
        evs: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                    if isinstance(o, dict) and "ts" in o and "t" in o:
                        evs.append(o)
                except Exception:
                    continue
        evs.sort(key=lambda x: int(x.get("ts", 0)))
        return evs

    def _load_csv(self, path: str) -> List[Dict[str, Any]]:
        # ts,symbol,interval,o,h,l,c,v
        evs: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    ts = int(float(row.get("ts") or 0))
                    sym = (row.get("symbol") or "").upper()
                    itv = row.get("interval") or self.cfg.default_interval
                    o = float(row.get("o", 0)); h = float(row.get("h", 0)); l = float(row.get("l", 0))
                    c = float(row.get("c", 0)); v = float(row.get("v", 0))
                except Exception:
                    continue
                evs.append({"t": "kline", "ts": ts, "symbol": sym, "interval": itv, "x": True, "T": ts, "o": o, "h": h, "l": l, "c": c, "v": v})
        evs.sort(key=lambda x: int(x.get("ts", 0)))
        return evs

    def _load(self) -> List[Dict[str, Any]]:
        p = self.cfg.src
        if not os.path.exists(p):
            log.warning("[REPLAY_ERR] 파일 없음: %s", p)
            return []
        if p.lower().endswith(".ndjson") or p.lower().endswith(".jsonl"):
            return self._load_ndjson(p)
        elif p.lower().endswith(".csv"):
            return self._load_csv(p)
        else:
            # 단일 JSON 배열도 허용
            try:
                with open(p, "r", encoding="utf-8") as f:
                    arr = json.load(f)
                evs = [x for x in arr if isinstance(x, dict) and "ts" in x and "t" in x]
                evs.sort(key=lambda x: int(x.get("ts", 0)))
                return evs
            except Exception:
                log.warning("[REPLAY_ERR] 포맷 인식 실패: %s", p)
                return []

    # ---- play ----
    def _push_event(self, ev: Dict[str, Any]) -> None:
        t = (ev.get("t") or "").lower()
        if t == "mark":
            sym = ev.get("symbol")
            price = float(ev.get("price") or 0.0)
            self.bus.update_mark(sym, price, int(ev.get("ts") or 0))
            log.debug("[REPLAY] mark %s %.4f", sym, price)
        elif t == "kline":
            sym = ev.get("symbol")
            itv = ev.get("interval") or self.cfg.default_interval
            k = {
                "x": bool(ev.get("x", True)),
                "T": int(ev.get("T") or ev.get("ts") or 0),
                "o": float(ev.get("o", 0)), "h": float(ev.get("h", 0)),
                "l": float(ev.get("l", 0)), "c": float(ev.get("c", 0)),
                "v": float(ev.get("v", 0)),
            }
            self.bus.update_kline(sym, itv, k)
            log.debug("[REPLAY] kline %s/%s c=%.4f", sym, itv, k["c"])
        elif t == "account":
            try:
                self.bus.set_account(ev.get("data") or {})
            except Exception:
                pass

    def _loop(self) -> None:
        events = self._load()
        if not events:
            log.warning("[REPLAY] 이벤트 없음: %s", self.cfg.src)
            return
        while not self._stop.is_set():
            base_ts = int(events[0]["ts"])
            start_real = time.time()
            for ev in events:
                if self._stop.is_set():
                    break
                ts = int(ev.get("ts") or 0)
                dt_real = (ts - base_ts) / max(1e-6, 1000.0 * self.cfg.speed)
                tgt = start_real + dt_real
                now = time.time()
                if tgt > now:
                    time.sleep(min(1.0, tgt - now))
                try:
                    self._push_event(ev)
                except Exception as e:
                    log.warning("[REPLAY_ERR] push 실패: %s", e)
            if not self.cfg.loop:
                break
            log.info("[REPLAY] eof → loop 재시작")
        log.info("[REPLAY] 종료")

    def start(self) -> None:
        if not self.cfg.enabled:
            log.info("[REPLAY] 비활성화")
            return
        if self._th and self._th.is_alive():
            return
        self._stop.clear()
        self._th = threading.Thread(target=self._loop, name="replay", daemon=True)
        self._th.start()

    def stop(self) -> None:
        self._stop.set()
