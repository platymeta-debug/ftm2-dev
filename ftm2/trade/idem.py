from __future__ import annotations

_TF2MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def tf_ms(tf: str) -> int:
    """Return timeframe duration in milliseconds."""

    return _TF2MS.get(tf, 300_000)


def make_idem_key(symbol: str, stance: str, anchor_tf: str, bar_ts: int) -> str:
    """Create a normalized idempotency key."""

    return f"{symbol.upper()}:{anchor_tf}:{int(bar_ts)}:{stance.upper()}"

