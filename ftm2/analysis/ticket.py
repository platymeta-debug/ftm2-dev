# -*- coding: utf-8 -*-
"""AMT builder based on multi-TF vote"""

# [ANCHOR:TICKET_SYNTH]
from typing import List, Dict
import os
from ftm2.ticket.model import AMTTicket, make_amt_id
from ftm2.config.aggr import load_aggr_profile


def _weights() -> List[int]:
    return [int(x) for x in os.getenv("TF_VOTE_WEIGHTS", "1,1,2,3").split(",")]


def _vote(details) -> dict:
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


def _plan_from_prof(state, symbol, best, prof) -> dict:
    price = state.marks.get(symbol)
    atr = best.ind.get("atr") or 0.0
    notional = float(state.monitor.get("equity", 0.0)) * prof["RISK_R"]
    qty = max(prof["MIN_NOTIONAL"], notional) / max(1e-9, float(price))
    return {
        "entry": "market",
        "qty": qty,
        "notional": max(prof["MIN_NOTIONAL"], notional),
        "risk_R": prof["RISK_R"],
        "sl_atr_mult": prof["SL_ATR"],
        "tp_ladder_R": prof["TP"],
        "cooldown_s": prof["COOLDOWN_S"],
        "min_notional": float(prof["MIN_NOTIONAL"]),
        "tif": "GTC"
    }


def build_amt(state, symbol: str, details: List) -> AMTTicket | None:
    prof = load_aggr_profile(state)
    vt = _vote(details)
    best = max(details, key=lambda d: (d.readiness.get("level") == "READY", d.score, d.p_up))
    lvl = best.readiness.get("level")
    if lvl != "READY":
        return None

    plan = _plan_from_prof(state, symbol, best, prof)
    side = "BUY" if best.direction == "LONG" else ("SELL" if best.direction == "SHORT" else "BUY")
    actions = [{"type": "adjust", "side": side, "qty": plan["qty"], "reduce_only": False}]

    amt: AMTTicket = {
        "id": make_amt_id(symbol),
        "symbol": symbol,
        "created_ts": __import__("time").time(),
        "aggr_level": prof["LEVEL"],
        "summary": {
            "direction": best.direction,
            "score": best.score,
            "p_up": best.p_up,
            "regime": best.regime,
            "rv_pr": best.ind.get("rv_pr"),
            "readiness": lvl,
            "gates": best.gates,
            "tf_vote": vt
        },
        "plan": plan,
        "actions": actions,
        "trace": {
            "contrib": best.contrib,
            "inputs": {"features": "…", "forecast": "…", "regimes": "…"},
            "thresholds": {"OPEN_TH": prof["OPEN_TH"], "PUP_TH": prof["PUP_TH"], "RV_BAND": prof["RV_BAND"], "REGIME_ALLOW": prof["REGIME_ALLOW"]}
        },
        "version": "AMT/1"
    }
    state.log.info("[AMT] build %s level=%d score=%+.2f gates=%s qty=%.6f",
                   symbol, prof["LEVEL"], best.score, best.gates, plan["qty"])
    return amt
