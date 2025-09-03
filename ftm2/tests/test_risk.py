import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.trade.risk import RiskEngine, RiskConfig


def _snapshot(extra=None):
    snap = {
        "forecasts": {("BTCUSDT", "1m"): {"stance": "LONG", "score": 0.6}},
        "features": {("BTCUSDT", "1m"): {"atr14": 2.0}},
        "klines": {("BTCUSDT", "1m"): {"T": 1, "x": True}},
        "marks": {"BTCUSDT": {"price": 100.0}},
        "risk": {},
        "account": {},
    }
    if extra:
        snap.update(extra)
    return snap


def test_risk_basic_and_cap():
    cfg = RiskConfig(
        risk_target_pct=0.5,
        corr_cap_per_side=0.5,
        atr_k=2.0,
        min_notional=0.0,
        equity_override=1000.0,
    )
    eng = RiskEngine(["BTCUSDT"], cfg)
    out = eng.process_snapshot(_snapshot())
    assert len(out) == 1
    t = out[0]
    assert t["reason"] == "CAP"
    assert pytest.approx(t["target_notional"], rel=1e-6) == 500.0
    assert pytest.approx(t["target_qty"], rel=1e-6) == 5.0


def test_day_cut():
    cfg = RiskConfig(
        risk_target_pct=0.3,
        day_max_loss_pct=3.0,
        atr_k=2.0,
        min_notional=0.0,
        equity_override=1000.0,
    )
    eng = RiskEngine(["BTCUSDT"], cfg)
    snap = _snapshot({"risk": {"day_pnl_pct": -3.5}})
    out = eng.process_snapshot(snap)
    t = out[0]
    assert t["target_qty"] == 0.0
    assert t["reason"] == "DAY_CUT"
    assert eng.day_cut_on
