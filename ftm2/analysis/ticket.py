# -*- coding: utf-8 -*-
"""Ticket builder based on multi-TF vote"""

# [ANCHOR:TICKET_BUILDER]
from typing import List, Dict
import os, time, hashlib, json


def _weights() -> List[int]:
    return [int(x) for x in os.getenv("TF_VOTE_WEIGHTS", "1,1,2,3").split(",")]


def _vote(details: List) -> Dict:
    tfs = os.getenv("TF_ORDER", "5m,15m,1h,4h").split(",")
    w = _weights()
    long_v, short_v, flat_v = 0, 0, 0
    flow = []
    for i, d in enumerate(details):
        dirc = d.direction
        wt = w[i] if i < len(w) else 1
        arrow = "↑" if dirc == "LONG" else ("↓" if dirc == "SHORT" else "→")
        flow.append(f"{tfs[i]} {arrow}")
        if dirc == "LONG":
            long_v += wt
        elif dirc == "SHORT":
            short_v += wt
        else:
            flat_v += wt
    return dict(long=long_v, short=short_v, flat=flat_v, flow=" / ".join(flow))


def _trace_id(symbol: str) -> str:
    raw = f"{symbol}-{time.time_ns()}"
    return "ANL-" + hashlib.md5(raw.encode()).hexdigest()[:10]


def synthesize_ticket(details: List) -> Dict | None:
    if not details:
        return None
    symbol = details[0].symbol
    # READY 레벨 중 최고 TF의 항목 선택
    ready_items = [d for d in details if d.readiness.get("level") == "READY"]
    best = max(ready_items, key=lambda d: (d.score, d.p_up), default=None)
    if not best:
        return None
    vt = _vote(details)
    reason = ["score>=OPEN_TH", "p_up>=PUP_TH", "regime_ok", "rv_band_ok", "risk_ok", "cooldown_ok"]
    ticket = {
        "symbol": symbol,
        "reason": reason,
        "dir": best.direction,
        "score": best.score,
        "p_up": best.p_up,
        "plan": best.plan,
        "tf_vote": {"long": vt["long"], "short": vt["short"], "flat": vt["flat"], "flow": vt["flow"]},
        "trace_id": _trace_id(symbol),
    }
    return ticket
# [ANCHOR:TICKET_BUILDER] end
