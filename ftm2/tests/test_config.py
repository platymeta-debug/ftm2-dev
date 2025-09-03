import sys
from pathlib import Path
import sys
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.core.config import load_forecast_cfg
from ftm2.signal.forecast import ForecastEnsemble, ForecastConfig
from ftm2.app import Orchestrator


class DummyDB:
    def __init__(self, data=None):
        self.data = data or {}

    def get_config(self, key: str):
        return self.data.get(key)

    def set(self, key: str, val: str):
        self.data[key] = val


def test_load_forecast_cfg_priority(monkeypatch):
    monkeypatch.setenv("FC_MR_SCALE", "30")
    monkeypatch.setenv("FC_STRONG_THR", "0.5")
    db = DummyDB({"forecast.strong_thr": "0.8"})
    cfg = load_forecast_cfg(db)
    assert cfg.strong_thr == 0.8
    assert cfg.mr_scale == 30.0
    base = ForecastConfig()
    assert cfg.flat_thr == base.flat_thr


def test_reload_cfg_loop_updates(monkeypatch):
    monkeypatch.delenv("FC_STRONG_THR", raising=False)
    db = DummyDB()
    fc = ForecastEnsemble(["BTCUSDT"], "1m", ForecastConfig())
    obj = type("Dummy", (), {})()
    obj.db = db
    obj.forecast = fc
    obj._stop = threading.Event()

    th = threading.Thread(target=Orchestrator._reload_cfg_loop, args=(obj, 0.1), daemon=True)
    th.start()
    time.sleep(0.2)
    db.set("forecast.strong_thr", "0.9")
    time.sleep(0.2)
    obj._stop.set()
    th.join(timeout=1.0)
    assert abs(obj.forecast.cfg.strong_thr - 0.9) < 1e-9
