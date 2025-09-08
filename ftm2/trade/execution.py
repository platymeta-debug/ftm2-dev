# -*- coding: utf-8 -*-
"""Order execution utilities with preflight checks."""
from __future__ import annotations

import math, os, time

ORDER_BACKOFF = {}  # {(symbol, reason): until_ts}


def _round_qty(qty, step, mode="down"):
    if step <= 0:
        return qty
    k = qty / step
    if mode == "down":
        k = math.floor(k + 1e-12)
    else:
        k = round(k)
    return k * step


def _apply_per_sym_cap(state, symbol, price, qty):
    cap = float(os.getenv("PER_SYM_LEVER_CAP", "0.80"))
    equity = max((state.snapshot().get("monitor") or {}).get("equity") or 0.0, 1e-9)
    max_notional = cap * equity
    max_qty = max_notional / max(price, 1e-9)
    return min(qty, max_qty)


def preflight_order(state, client, intent: dict) -> tuple:
    """
    intent: {symbol, side, type, qty_raw, price?}
    returns: (ok:bool, qty_final:float, reason:str)
    """
    symbol = intent["symbol"]
    side = intent["side"]
    otype = intent["type"]
    price = float(intent.get("price") or (state.snapshot()["marks"].get(symbol) or {}).get("price") or 0.0)
    qty = float(intent["qty_raw"])

    cool_s = int(os.getenv("ORDER_COOLDOWN_S", "2"))
    key = (symbol, "4005")
    until = ORDER_BACKOFF.get(key, 0)
    if time.time() < until:
        return (False, 0.0, f"BACKOFF_UNTIL:{until}")

    qty = _apply_per_sym_cap(state, symbol, price, qty)

    filters = client.get_symbol_filters(symbol) or {}
    lot = filters.get("lot_size") or {}
    mlot = filters.get("market_lot_size") or {}
    notional = filters.get("notional") or {}
    step = (mlot if otype == "MARKET" else lot).get("stepSize") or 0.0
    maxQty = (mlot if otype == "MARKET" else lot).get("maxQty") or float("inf")
    minQty = (mlot if otype == "MARKET" else lot).get("minQty") or 0.0
    qty = max(0.0, min(qty, maxQty))
    qty = _round_qty(qty, step, os.getenv("QTY_ROUNDING", "down"))

    if qty < minQty:
        mode = os.getenv("MIN_NOTIONAL_MODE", "auto_bump")
        if mode == "skip":
            state.log.warning(f"[ORDER_BLOCKED] QTY_TOO_SMALL min={minQty}")
            return (False, 0.0, "QTY_TOO_SMALL")
        else:
            qty = minQty

    minNotional = float(notional.get("minNotional") or 0.0)
    if minNotional > 0 and price * qty < minNotional:
        mode = os.getenv("MIN_NOTIONAL_MODE", "auto_bump")
        need_qty = minNotional / max(price, 1e-9)
        if mode == "skip":
            state.log.warning(f"[ORDER_BLOCKED] BELOW_MIN_NOTIONAL min={minNotional}")
            return (False, 0.0, "BELOW_MIN_NOTIONAL")
        qty = min(need_qty, maxQty)
        qty = _round_qty(qty, step, os.getenv("QTY_ROUNDING", "down"))
        if qty * price < minNotional:
            state.log.warning(f"[ORDER_BLOCKED] CANNOT_MEET_MIN_NOTIONAL min={minNotional}")
            return (False, 0.0, "CANNOT_MEET_MIN_NOTIONAL")

    state.log.info(
        f"[ORDER_PREFLIGHT] {symbol} {side} type={otype} step={step} maxQty={maxQty} price={price} qty_final={qty}"
    )
    if qty <= 0:
        return (False, 0.0, "QTY_ZERO")
    return (True, qty, "OK")


def place_order(state, client, intent: dict):
    ok, qty, reason = preflight_order(state, client, intent)
    if not ok:
        return {"status": "blocked", "reason": reason}

    payload = dict(intent)
    payload["quantity"] = qty

    resp = client.post_order(payload)
    if resp.get("error_code") == -4005:
        ORDER_BACKOFF[(intent["symbol"], "4005")] = time.time() + int(os.getenv("ORDER_COOLDOWN_S", "2"))
        state.log.warning("[ORDER_BACKOFF] -4005 cool applied")
    return resp

