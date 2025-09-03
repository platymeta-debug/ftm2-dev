import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.signal.regime import RegimeClassifier, RegimeConfig


def test_regime_classifier_trend_up():
    rc = RegimeClassifier(["BTCUSDT"], "1m", RegimeConfig())
    snap = {
        "features": {("BTCUSDT", "1m"): {"ema_fast": 1.2, "ema_slow": 1.0, "pr_rv20": 0.5, "T": 1}},
        "now_ts": 1,
    }
    rows = rc.process_snapshot(snap)
    assert len(rows) == 1
    reg = rows[0]["regime"]
    assert reg["code"] == "TREND_UP"
    assert reg["age"] == 0

    snap2 = {
        "features": {("BTCUSDT", "1m"): {"ema_fast": 1.2, "ema_slow": 1.0, "pr_rv20": 0.5, "T": 2}},
        "now_ts": 2,
    }
    rows2 = rc.process_snapshot(snap2)
    assert rows2 == []
