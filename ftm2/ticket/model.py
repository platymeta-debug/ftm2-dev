from typing import TypedDict, List, Dict, Any
from dataclasses import dataclass
import time

class AMTSummary(TypedDict):
    direction: str
    score: float
    p_up: float
    regime: str
    rv_pr: float | None
    readiness: str
    gates: Dict[str, bool]
    tf_vote: Dict[str, Any]  # {"flow": "5m ↑ / ...", "long": int, "short": int}

class AMTPlan(TypedDict):
    entry: str
    qty: float
    notional: float
    risk_R: float
    sl_atr_mult: float
    tp_ladder_R: List[float]
    cooldown_s: int
    min_notional: float
    tif: str

class AMTAction(TypedDict):
    type: str      # "adjust"
    side: str      # "BUY"|"SELL"
    qty: float
    reduce_only: bool

class AMTTicket(TypedDict):
    id: str
    symbol: str
    created_ts: float
    aggr_level: int
    summary: AMTSummary
    plan: AMTPlan
    actions: List[AMTAction]
    trace: Dict[str, Any]
    version: str

def make_amt_id(symbol: str, ts: float | None = None) -> str:
    if ts is None: ts = time.time()
    return f"amt/{symbol}/{int(ts*1000)}"
