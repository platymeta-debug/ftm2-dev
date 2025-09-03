import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.data.features import FeatureEngine, FeatureConfig


def test_feature_engine_process_snapshot():
    eng = FeatureEngine(["BTCUSDT"], ["1m"], FeatureConfig())

    snap1 = {"klines": {("BTCUSDT", "1m"): {"o": 1, "h": 1, "l": 1, "c": 1, "x": True, "T": 1}}}
    eng.process_snapshot(snap1)

    snap2 = {"klines": {("BTCUSDT", "1m"): {"o": 1, "h": 1.2, "l": 0.9, "c": 1.1, "x": True, "T": 2}}}
    rows = eng.process_snapshot(snap2)
    assert len(rows) == 1
    feats = rows[0]["features"]
    assert feats["ret1"] == pytest.approx(0.1)
