# -*- coding: utf-8 -*-
"""
Ops HTTPD: 헬스/레디/메트릭/KPI 노출 (표준 라이브러리만 사용)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading, time, json, logging

try:
    from ftm2.core.state import StateBus
except Exception:
    from core.state import StateBus  # type: ignore

log = logging.getLogger("ftm2.ops.http")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@dataclass
class OpsHttpConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    ready_max_skew_s: float = 15.0  # 최신 mark ts와 현재의 최대 허용 지연

class _Handler(BaseHTTPRequestHandler):
    # 주입: 클래스 변수로 공유
    bus: Optional[StateBus] = None
    cfg: Optional[OpsHttpConfig] = None

    def _write(self, code: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, fmt, *args):
        # 표준 http.server noisy 로그 억제 → 필요시 INFO로
        log.debug("[OPS_HTTP] " + fmt, *args)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p == "/healthz":
                return self._write(200, "ok")
            if p == "/readyz":
                return self._readyz()
            if p == "/metrics":
                return self._metrics()
            if p == "/kpi":
                return self._kpi()
            return self._write(404, "not found")
        except Exception as e:
            log.warning("[OPS_HTTP][ERR] %s %s", p, e)
            return self._write(500, "error")

    # ---- endpoints ----
    def _readyz(self):
        snap = self.bus.snapshot() if self.bus else {}
        now_ms = int(time.time() * 1000)
        marks = snap.get("marks") or {}
        # 최신 가격 이벤트 나이(초)
        ages = []
        for m in marks.values():
            ts = int(m.get("ts") or 0)
            if ts > 0:
                ages.append(max(0.0, (now_ms - ts) / 1000.0))
        max_age = max(ages) if ages else 9e9
        ok = (max_age <= float(self.cfg.ready_max_skew_s if self.cfg else 15.0))
        body = "ready" if ok else f"stale(max_age={max_age:.1f}s)"
        return self._write(200 if ok else 503, body)

    def _metrics(self):
        snap = self.bus.snapshot() if self.bus else {}
        now_s = int(time.time())
        boot_ts = int(snap.get("boot_ts") or now_s * 1000)
        uptime = max(0, now_s - int(boot_ts / 1000))

        risk = snap.get("risk") or {}
        equity = float(risk.get("equity") or 0.0)
        day_pnl_pct = float(risk.get("day_pnl_pct") or 0.0)
        guard = snap.get("guard") or {}
        eq = guard.get("exec_quality") or {}
        ol = guard.get("exec_ledger") or {}
        open_orders = sum(len(v) for v in (snap.get("open_orders") or {}).values())

        # Prometheus text format (v0.0.4)
        lines = []
        lines.append("# HELP ftm2_uptime_seconds Process uptime in seconds")
        lines.append("# TYPE ftm2_uptime_seconds gauge")
        lines.append(f"ftm2_uptime_seconds {uptime}")

        lines.append("# HELP ftm2_equity Account equity")
        lines.append("# TYPE ftm2_equity gauge")
        lines.append(f"ftm2_equity {equity}")

        lines.append("# HELP ftm2_leverage Notional / Equity")
        lines.append("# TYPE ftm2_leverage gauge")
        # 레버리지 계산
        notional = 0.0
        for sym, p in (snap.get("positions") or {}).items():
            qty = float(p.get("pa") or 0.0)
            px = float((snap.get("marks") or {}).get(sym, {}).get("price") or 0.0)
            notional += abs(qty) * px
        lever = (notional / equity) if equity > 0 else 0.0
        lines.append(f"ftm2_leverage {lever}")

        lines.append("# HELP ftm2_open_orders Current open orders")
        lines.append("# TYPE ftm2_open_orders gauge")
        lines.append(f"ftm2_open_orders {open_orders}")

        lines.append("# HELP ftm2_exec_slip_bps_p90 Rolling p90 slippage (bps)")
        lines.append("# TYPE ftm2_exec_slip_bps_p90 gauge")
        p90 = float((eq.get("slip_bps_overall") or {}).get("p90") or 0.0)
        lines.append(f"ftm2_exec_slip_bps_p90 {p90}")

        lines.append("# HELP ftm2_orders_total Orders in window")
        lines.append("# TYPE ftm2_orders_total gauge")
        lines.append(f"ftm2_orders_total {int(ol.get('orders') or 0)}")

        lines.append("# HELP ftm2_fill_rate Fill rate in window")
        lines.append("# TYPE ftm2_fill_rate gauge")
        lines.append(f"ftm2_fill_rate {float(ol.get('fill_rate') or 0.0)}")

        return self._write(200, "\n".join(lines) + "\n", content_type="text/plain; version=0.0.4")

    def _kpi(self):
        snap = self.bus.snapshot() if self.bus else {}
        kpi = (snap.get("monitor") or {}).get("kpi") or {}
        return self._write(200, json.dumps(kpi, ensure_ascii=False), content_type="application/json; charset=utf-8")

class OpsHTTPD:
    def __init__(self, bus: StateBus, cfg: OpsHttpConfig) -> None:
        self.bus = bus
        self.cfg = cfg
        self._th: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._server: Optional[HTTPServer] = None

    def start(self) -> None:
        if not self.cfg.enabled:
            log.info("[OPS_HTTP] disabled")
            return
        if self._th and self._th.is_alive():
            return
        _Handler.bus = self.bus
        _Handler.cfg = self.cfg
        self._server = HTTPServer((self.cfg.host, int(self.cfg.port)), _Handler)
        self._stop.clear()
        def _serve():
            log.info("[OPS_HTTP] start on %s:%s", self.cfg.host, self.cfg.port)
            try:
                while not self._stop.is_set():
                    self._server.handle_request()
            except Exception as e:
                log.warning("[OPS_HTTP][ERR] %s", e)
            log.info("[OPS_HTTP] stop")
        self._th = threading.Thread(target=_serve, name="ops-http", daemon=True)
        self._th.start()

    def stop(self) -> None:
        self._stop.set()
        # 더미 요청으로 깨어나게 할 수도 있으나, 데몬 스레드이므로 생략
