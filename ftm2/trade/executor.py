from __future__ import annotations

from typing import Dict
import time
import logging

from ftm2.trade.idem import tf_ms
from ftm2.trade.router import OrderRouter, Target
from ftm2.risk.engine import RiskEngine
from ftm2.risk.gates import GateKeeper
from ftm2.notify.discord import Alerts
from ftm2.monitor.kpi import KPIEngine

log = logging.getLogger("ftm2.exec.orch")


class Executor:
    """Run forecast -> gates -> risk sizing -> order -> KPI/Alerts pipeline."""

    def __init__(self, router: OrderRouter, kpi: KPIEngine, alerts: Alerts) -> None:
        self.router = router
        self.risk = RiskEngine()
        self.gates = GateKeeper()
        self.kpi = kpi
        self.alerts = alerts

    def route(self, forecast: Dict, state) -> Dict:
        symbol = forecast["symbol"]
        stance = forecast.get("stance", "FLAT")
        side = "BUY" if stance == "LONG" else ("SELL" if stance == "SHORT" else None)
        if side is None:
            return {"status": "skipped", "reason": "stance_flat"}

        mark_map = getattr(state, "mark", {})
        mark_px = (mark_map.get(symbol) or {}).get("mark", 0.0)

        ctx = {
            "symbol": symbol,
            "forecast": forecast,
            "features": getattr(state, "features", {}),
            "regime": getattr(state, "regime_map", {}).get(symbol, {"trend": "FLAT"}),
            "risk_ctx": {},
            "positions": getattr(state, "positions", []),
            "account": getattr(state, "account", {}),
            "mark_price": mark_px,
        }

        gate_res = self.gates.evaluate(ctx)
        if not gate_res.get("allow", False):
            self.kpi.on_event({"type": "signal_blocked", "symbol": symbol, "blocked": gate_res.get("blocked", [])})
            return {"status": "skipped", "reason": "gate:" + ",".join(gate_res.get("blocked", []))}

        risk_res = self.risk.size_order(
            symbol=symbol,
            side=side,
            features=ctx["features"],
            regime=ctx["regime"],
            account=ctx["account"],
            positions=ctx["positions"],
            mark_price=mark_px,
            kline_map=getattr(state, "kline_map", {}),
            pnl_daily=getattr(state, "pnl_daily", 0.0),
        )
        if risk_res.qty <= 0:
            return {"status": "skipped", "reason": risk_res.reason}

        target = Target(
            symbol=symbol,
            side=side,
            action="ENTER",
            qty=risk_res.qty,
            reduce_only=False,
            meta={"link_id": None},
        )

        anchor_tf = forecast.get("tf", "5m")
        bar_ts = forecast.get("bar_ts")
        try:
            bar_ts = int(bar_ts) if bar_ts is not None else None
        except (TypeError, ValueError):
            bar_ts = None
        if bar_ts is None:
            span = max(tf_ms(anchor_tf), 1)
            now_ms = int(time.time() * 1000)
            bar_ts = now_ms - (now_ms % span)

        result = self.router.submit(target, anchor_tf=anchor_tf, tf_bar_ts=bar_ts)
        attempt_evt = {
            "type": "order_attempt",
            "symbol": symbol,
            "side": side,
            "qty": risk_res.qty,
            "status": result.get("status"),
        }
        self.kpi.on_event(attempt_evt)

        if result.get("status") == "sent":
            link_id = result.get("link_id")
            self.alerts.ticket_issued(
                symbol=symbol,
                side=side,
                qty=risk_res.qty,
                notional=risk_res.notional,
                price=mark_px,
                reason=f"{forecast.get('readiness', '?')} {stance}",
                link_id=link_id,
            )
            self.kpi.on_event({"type": "order_sent", "symbol": symbol, "side": side, "qty": risk_res.qty, "link_id": link_id})
            return {"status": "sent", "order": result.get("order"), "link_id": link_id}

        return {"status": result.get("status"), "reason": result.get("reason", "")}
