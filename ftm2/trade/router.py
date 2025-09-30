from __future__ import annotations

import logging
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ftm2.db.core import config_get, idem_reserve
from ftm2.exchange.binance import BinanceClient
from ftm2.trade.idem import make_idem_key, tf_ms

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
        self.idem_grace_ms = 5_000  # allow slight drift beyond bar close

    def _cooldown_ok(self) -> bool:
        now = time.time()
        if now - self._last_submit_ts < EXEC_COOLDOWN:
            log.info("ORD.SKIP.COOLDOWN %.2fs", EXEC_COOLDOWN - (now - self._last_submit_ts))
            return False
        self._last_submit_ts = now
        return True

    # [ANCHOR:EXEC_ROUNDING]
    def _round_by(self, x: float, step: float) -> float:
        if step <= 0:
            return x
        k = math.floor((x + 1e-12) / step)
        return k * step

    def _apply_filters(self, symbol: str, qty: float, ref_price: float) -> dict:
        f = self.cli.get_symbol_filters(symbol)
        step = float(f.get("stepSize", 1e-6))
        tick = float(f.get("tickSize", 1e-6))
        min_qty = float(f.get("minQty", 0.0))
        min_notional = float(f.get("minNotional", 0.0))

        qty_rounded = self._round_by(max(0.0, qty), step)
        px_rounded = self._round_by(ref_price, tick) if ref_price else ref_price

        notional = (px_rounded or ref_price or 0.0) * qty_rounded
        ok_qty = qty_rounded >= max(min_qty, 0.0)
        ok_notional = (min_notional <= 0.0) or (notional >= min_notional)
        return {
            "qty": qty_rounded,
            "price": px_rounded,
            "notional": notional,
            "ok": bool(ok_qty and ok_notional),
            "why": []
            if (ok_qty and ok_notional)
            else [
                *(["min_qty"] if not ok_qty else []),
                *(["min_notional"] if not ok_notional else []),
            ],
        }

    def _allowed_slip_bps(self, symbol: str) -> float:
        sym = symbol.upper()
        for key in (f"slippage.bps.{sym}", "slippage.bps.*"):
            raw = config_get(key, None)
            if raw is None:
                continue
            try:
                return float(raw)
            except Exception:
                log.debug("SLIP.CONFIG.INVALID key=%s value=%s", key, raw)
                continue
        return EXEC_SLIPPAGE_BPS

    def _slip_ok(self, ref_px: Optional[float], mkt_px: float, allowed_bps: float) -> bool:
        if ref_px is None or ref_px == 0:
            return True
        bps = abs(mkt_px - ref_px) / ref_px * 1e4
        return bps <= allowed_bps

    def submit(
        self,
        target: Target,
        anchor_tf: str = "5m",
        tf_bar_ts: Optional[int] = None,
    ) -> dict:
        """Submit a market order with idem check and retry logic."""
        if not self._cooldown_ok():
            return {"status": "skipped", "reason": "cooldown"}

        sym = target.symbol.upper()
        side = "BUY" if target.side.upper().startswith("B") else "SELL"

        try:
            mark = self.cli.get_mark_price(sym)
            mark_px = float(mark["markPrice"])
        except Exception as exc:
            log.error("ORD.MARK_FAIL %s", exc)
            return {"status": "rejected", "reason": "mark_fail"}

        allowed_bps = self._allowed_slip_bps(sym)
        if not self._slip_ok(target.px, mark_px, allowed_bps):
            log.warning(
                "ORD.SKIP.SLIPPAGE ref=%.8f m=%.8f bps>%.1f",
                target.px,
                mark_px,
                allowed_bps,
            )
            return {"status": "skipped", "reason": "slippage_sym"}

        span_ms = max(tf_ms(anchor_tf), 1)
        if tf_bar_ts is None:
            now_ms = int(time.time() * 1000)
            tf_bar_ts = now_ms - (now_ms % span_ms)
        stance = "LONG" if side == "BUY" else "SHORT"
        idem_key = make_idem_key(sym, stance, anchor_tf, tf_bar_ts)
        ttl_ms = span_ms + self.idem_grace_ms
        if not idem_reserve(idem_key, sym, side, anchor_tf, tf_bar_ts, ttl_ms):
            log.info("ORD.SKIP.IDEM %s", idem_key)
            return {"status": "skipped", "reason": "duplicate_bar"}

        filt = self._apply_filters(sym, target.qty, mark_px)
        if not filt["ok"] or filt["qty"] <= 0:
            return {
                "status": "skipped",
                "reason": "filter:" + ",".join(filt["why"] or ["qty_zero"]),
            }

        qty = filt["qty"]

        client_id = (target.meta or {}).get("link_id") or f"ftm2.{uuid.uuid4().hex[:20]}"

        attempt = 0
        while attempt < EXEC_MAX_RETRY:
            attempt += 1
            try:
                order = self.cli.create_order(
                    symbol=sym,
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

