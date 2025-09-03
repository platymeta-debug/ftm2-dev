import sqlite3
from ftm2.core.persistence import Persistence

def test_persistence_basic(tmp_path):
    db_path = tmp_path / "t.db"
    p = Persistence(str(db_path))
    p.ensure_schema()

    assert db_path.exists()

    p.record_event("INFO", "test", "hello", ts=1)
    p.save_patch("v1", "title", ts=2)
    p.upsert_config("k", "v1")
    p.upsert_config("k", "v2")
    assert p.get_config("k") == "v2"
    p.save_trade({"ts": 3, "symbol": "BTCUSDT", "qty": 1, "px": 100})
    p.upsert_position("BTCUSDT", qty=1.0, avg_px=100, updated_ts=4)
    p.upsert_position("BTCUSDT", qty=2.0, avg_px=200, updated_ts=5)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from events").fetchone()[0] == 1
        assert conn.execute("select count(*) from patches").fetchone()[0] == 1
        assert conn.execute("select val from config where key='k'").fetchone()[0] == "v2"
        assert conn.execute("select count(*) from trades").fetchone()[0] == 1
        assert conn.execute("select qty from positions where symbol='BTCUSDT'").fetchone()[0] == 2.0

    p.close()
