from __future__ import annotations

import logging
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ftm2.exchange.binance import BinanceClient

log = logging.getLogger("ftm2.exec")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except Exception:
        return default


EXEC_COOLDOWN = _env_float("EXEC_COOLDOWN_S", 1.0)
EXEC_SLIPPAGE_BPS = _env_float("EXEC_SLIPPAGE_BPS", 5.0)
EXEC_MAX_RETRY = int(os.getenv("EXEC_MAX_RETRY", "3"))


@dataclass
class Target:
    symbol: str
    side: str  # BUY | SELL
    action: str  # ENTER | ADD | REDUCE | EXIT
    qty: float
    px: Optional[float] = None
    reduce_only: bool = False
    meta: Dict[str, Any] | None = None


class OrderRouter:
    def __init__(self, cli: BinanceClient) -> None:
        self.cli = cli
        self._last_submit_ts = 0.0
        self._idem_bar_key: set[str] = set()

    def _cooldown_ok(self) -> bool:
        now = time.time()
        if now - self._last_submit_ts < EXEC_COOLDOWN:
            log.info("ORD.SKIP.COOLDOWN %.2fs", EXEC_COOLDOWN - (now - self._last_submit_ts))
            return False
        self._last_submit_ts = now
        return True

    def _qty_round(self, qty: float) -> float:
        step = 1e-6
        return math.floor(qty / step) * step

    def _slip_ok(self, ref_px: Optional[float], mkt_px: float) -> bool:
        if ref_px is None or ref_px == 0:
            return True
        bps = abs(mkt_px - ref_px) / ref_px * 1e4
        return bps <= EXEC_SLIPPAGE_BPS

    def submit(self, target: Target, tf_bar_ts: Optional[int] = None) -> dict:
        """Submit a market order with idem check and retry logic."""
        if not self._cooldown_ok():
            return {"status": "skipped", "reason": "cooldown"}

        idem_key: Optional[str] = None
        if tf_bar_ts is not None:
            idem_key = f"{target.symbol}:{tf_bar_ts}:{target.side}:{target.action}"
            if idem_key in self._idem_bar_key:
                log.info("ORD.DUPLICATE %s", idem_key)
                return {"status": "skipped", "reason": "duplicate_bar"}
            self._idem_bar_key.add(idem_key)

        try:
            mark = self.cli.get_mark_price(target.symbol)
            mark_px = float(mark["markPrice"])
        except Exception as exc:
            log.error("ORD.MARK_FAIL %s", exc)
            return {"status": "rejected", "reason": "mark_fail"}

        if not self._slip_ok(target.px, mark_px):
            log.warning(
                "ORD.SKIP.SLIPPAGE ref=%.8f m=%.8f bps>%.1f",
                target.px,
                mark_px,
                EXEC_SLIPPAGE_BPS,
            )
            return {"status": "skipped", "reason": "slippage"}

        qty = max(0.0, self._qty_round(target.qty))
        if qty <= 0:
            return {"status": "skipped", "reason": "qty_zero"}

        side = "BUY" if target.side.upper().startswith("B") else "SELL"
        client_id = (target.meta or {}).get("link_id") or f"ftm2.{uuid.uuid4().hex[:20]}"

        attempt = 0
        while attempt < EXEC_MAX_RETRY:
            attempt += 1
            try:
                order = self.cli.create_order(
                    symbol=target.symbol,
                    side=side,
                    type="MARKET",
                    qty=qty,
                    price=None,
                    reduce_only=target.reduce_only,
                    client_id=client_id,
                )
                log.info(
                    "ORD.SENT %s %s qty=%.8f id=%s",
                    target.symbol,
                    side,
                    qty,
                    order.get("orderId") if isinstance(order, dict) else None,
                )
                return {"status": "sent", "order": order, "link_id": client_id}
            except Exception as exc:
                msg = str(exc)
                retryable_codes = ["-1001", "-1013", "-1021", "-1100", "-2019"]
                retryable = any(code in msg for code in retryable_codes)
                if retryable and attempt < EXEC_MAX_RETRY:
                    log.warning("ORD.RETRYABLE attempt=%d %s", attempt, msg)
                    time.sleep(0.2 * attempt)
                    continue
                log.error("ORD.FATAL %s", msg)
                return {"status": "rejected", "reason": "fatal", "error": msg}

        return {"status": "rejected", "reason": "retry_exhausted"}

