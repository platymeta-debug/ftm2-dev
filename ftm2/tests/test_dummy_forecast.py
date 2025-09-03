import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.signal.dummy import DummyForecaster
from ftm2.discord_bot import notify


def test_dummy_forecaster_closed_bar():
    f = DummyForecaster(["BTCUSDT"], "1m")
    snap = {
        "klines": {("BTCUSDT", "1m"): {"o": 1.0, "c": 2.0, "x": True, "T": 123}},
    }
    out = f.evaluate(snap)
    assert out and out[0]["side"] == "LONG"
    # same bar processed again -> no output
    assert f.evaluate(snap) == []


def test_enqueue_alert(tmp_path, monkeypatch):
    q = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(notify, "QUEUE", str(q))
    assert notify.enqueue_alert("hello")
    data = q.read_text(encoding="utf-8").strip()
    rec = json.loads(data)
    assert rec["text"] == "hello"
    assert rec["channel"] == "alerts"
