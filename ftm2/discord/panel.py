# -*- coding: utf-8 -*-
"""Discord KPI panel rendering."""
from __future__ import annotations


def render_kpi_message(state) -> str:
    k = (state.snapshot().get("monitor") or {}).get("kpi", {})
    equity = k.get("equity", 0.0)
    day_pnl_pct = k.get("day_pnl_pct", 0.0)
    exp = k.get("exposure", {})
    port_lev = k.get("port_leverage", 0.0)

    exp_line = f"📐 익스포저: 롱 {exp.get('long_pct',0.0):.2%} / 숏 {exp.get('short_pct',0.0):.2%}"
    if (exp.get("long_target", 0.0) + exp.get("short_target", 0.0)) > 0:
        exp_line += (
            f" (실제 L {exp.get('long_actual',0.0):.2%} / S {exp.get('short_actual',0.0):.2%}, "
            f"타깃 L {exp.get('long_target',0.0):.2%} / S {exp.get('short_target',0.0):.2%})"
        )
    else:
        exp_line += (
            f" (실제 L {exp.get('long_actual',0.0):.2%} / S {exp.get('short_actual',0.0):.2%})"
        )

    msg = []
    msg.append("📊 FTM2 KPI 대시보드")
    msg.append("─────────────────────────────────")
    msg.append(f"⏱️ 가동시간: {state.uptime_s()//60}분")
    msg.append(f"💰 자본(Equity): {equity:,.2f}  | 포트 레버리지: {port_lev:.2f}x")
    msg.append(f"📉 당일손익: {day_pnl_pct:.2f}%")
    msg.append(exp_line)
    return "\n".join(msg)

