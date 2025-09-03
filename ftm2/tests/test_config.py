import sys
from pathlib import Path
import sys
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.core.config import load_forecast_cfg, load_risk_cfg
from ftm2.signal.forecast import ForecastEnsemble, ForecastConfig
from ftm2.trade.risk import RiskEngine, RiskConfig
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



def test_load_risk_cfg_priority(monkeypatch):
    monkeypatch.setenv("CORR_CAP_PER_SIDE", "0.5")
    db = DummyDB({"risk.target_pct": "0.4"})
    cfg = load_risk_cfg(db)
    assert cfg.risk_target_pct == 0.4
    assert cfg.corr_cap_per_side == 0.5
    assert cfg.atr_k == 2.0


def test_reload_cfg_loop_updates(monkeypatch):
    monkeypatch.delenv("FC_STRONG_THR", raising=False)
    monkeypatch.delenv("RISK_ATR_K", raising=False)
    db = DummyDB()
    fc = ForecastEnsemble(["BTCUSDT"], "1m", ForecastConfig())
    rc = RiskEngine(["BTCUSDT"], RiskConfig())
    obj = type("Dummy", (), {})()
    obj.db = db
    obj.forecast = fc
    obj.risk = rc
    obj._stop = threading.Event()

    th = threading.Thread(target=Orchestrator._reload_cfg_loop, args=(obj, 0.1), daemon=True)
    th.start()
    time.sleep(0.2)
    db.set("forecast.strong_thr", "0.9")
    db.set("risk.atr_k", "3.0")
    time.sleep(0.2)
    obj._stop.set()
    th.join(timeout=1.0)
    assert abs(obj.forecast.cfg.strong_thr - 0.9) < 1e-9
    assert abs(obj.risk.cfg.atr_k - 3.0) < 1e-9

