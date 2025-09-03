import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.signal.forecast import ForecastEnsemble, ForecastConfig


def test_forecast_ensemble_process_snapshot():
    fe = ForecastEnsemble(["BTCUSDT"], "1m", ForecastConfig())
    snap1 = {
        "features": {("BTCUSDT", "1m"): {"ema_fast": 1.1, "ema_slow": 1.0, "rsi14": 40, "ret1": 0.01, "rv20": 0.02, "rng_atr": 0.5}},
        "regimes": {("BTCUSDT", "1m"): {"code": "TREND_UP"}},
        "klines": {("BTCUSDT", "1m"): {"x": True, "T": 1}},
    }
    rows1 = fe.process_snapshot(snap1)
    assert len(rows1) == 1
    fc1 = rows1[0]["forecast"]
    assert -1.0 <= fc1["score"] <= 1.0
    assert 0.0 <= fc1["prob_up"] <= 1.0
    assert fc1["regime"] == "TREND_UP"

    snap2 = {
        "features": {("BTCUSDT", "1m"): {"ema_fast": 1.2, "ema_slow": 1.0, "rsi14": 60, "ret1": 0.02, "rv20": 0.02, "rng_atr": 0.5}},
        "regimes": {("BTCUSDT", "1m"): {"code": "TREND_UP"}},
        "klines": {("BTCUSDT", "1m"): {"x": True, "T": 2}},
    }
    rows2 = fe.process_snapshot(snap2)
    assert len(rows2) == 1
    assert len(fe._perf) == 3
    assert fe.process_snapshot(snap2) == []
