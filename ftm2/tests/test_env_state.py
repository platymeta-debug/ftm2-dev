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
    bus.set_positions([{"symbol": "BTCUSDT"}])
    bus.set_account({"balance": 1})
    snap = bus.snapshot()
    assert snap["marks"]["BTCUSDT"]["price"] == 100.0
    assert snap["klines"][("BTCUSDT", "1m")]["o"] == 1
    assert snap["positions"][0]["symbol"] == "BTCUSDT"
    assert snap["account"]["balance"] == 1
    assert snap["features"] == {}
    assert isinstance(snap["boot_ts"], int)
    assert isinstance(snap["now_ts"], int)
