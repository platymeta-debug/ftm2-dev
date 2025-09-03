import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from ftm2.core.env import load_env_chain
from ftm2.core.state import StateBus


def test_load_env_chain(tmp_path, monkeypatch):
    token_file = tmp_path / "token.env"
    token_file.write_text("KEY1=token\nKEY2=token\n")
    env_file = tmp_path / ".env"
    env_file.write_text("KEY2=env\nKEY3=env\n")

    monkeypatch.setenv("KEY1", "os")

    env = load_env_chain(paths=(str(token_file), str(env_file)))
    assert env["KEY1"] == "os"
    assert env["KEY2"] == "token"
    assert env["KEY3"] == "env"


def test_state_bus_snapshot():
    bus = StateBus()
    bus.update_mark("BTCUSDT", 100.0, 123)
    bus.update_kline("BTCUSDT", "1m", {"o":1})
    bus.set_positions({"BTCUSDT": {"symbol": "BTCUSDT"}})
    bus.set_account({"balance": 1})
    bus.update_forecast("BTCUSDT", "1m", {"score": 0.1})
    bus.set_targets({"BTCUSDT": {"target_qty": 1.0}})
    bus.set_risk_state({"equity": 1000})
    bus.set_open_orders({"BTCUSDT": [{"orderId": "1"}]})
    snap = bus.snapshot()
    assert snap["marks"]["BTCUSDT"]["price"] == 100.0
    assert snap["klines"][("BTCUSDT", "1m")]["o"] == 1
    assert snap["positions"]["BTCUSDT"]["symbol"] == "BTCUSDT"
    assert snap["account"]["balance"] == 1
    assert snap["features"] == {}
    assert snap["regimes"] == {}
    assert snap["forecasts"][('BTCUSDT', '1m')]["score"] == 0.1
    assert snap["targets"]["BTCUSDT"]["target_qty"] == 1.0
    assert snap["risk"]["equity"] == 1000
    assert snap["open_orders"]["BTCUSDT"][0]["orderId"] == "1"
    assert isinstance(snap["boot_ts"], int)
    assert isinstance(snap["now_ts"], int)


def test_state_bus_fills_queue():
    bus = StateBus()
    bus.push_fill({"symbol": "BTCUSDT", "side": "BUY"})
    bus.push_fill({"symbol": "ETHUSDT", "side": "SELL"})
    out = bus.drain_fills()
    assert len(out) == 2
    assert out[0]["symbol"] == "BTCUSDT"
    assert bus.drain_fills() == []
