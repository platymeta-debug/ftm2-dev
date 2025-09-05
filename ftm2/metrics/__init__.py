"""Portfolio exposure and leverage utilities."""

from __future__ import annotations

from typing import Dict, Any, Tuple, List


def calc_exposure_and_leverage(
    snapshot: Dict[str, Any], marks: Dict[str, float], mode: str = "gross"
) -> Tuple[float, float]:
    """Return (gross_notional, leverage) based on positions in snapshot.

    Args:
        snapshot: account_snapshot() output containing ``equity`` and ``positions``.
        marks: symbol -> mark price mapping.
        mode: ``gross`` (default) uses sum(|qty*price|) / equity,
              ``net`` uses |sum(qty*price)| / equity.
    """

    equity = max(float(snapshot.get("equity", 0.0)), 1e-9)
    gross = 0.0
    net = 0.0
    for p in snapshot.get("positions", []):
        try:
            sym = p.get("symbol")
            amt = float(p.get("positionAmt") or 0.0)
        except Exception:
            continue
        if amt == 0:
            continue
        mark = float(marks.get(sym) or p.get("markPrice") or p.get("entryPrice") or 0.0)
        notional = amt * mark
        gross += abs(notional)
        net += notional
    lev = abs(net) / equity if mode == "net" else gross / equity
    return gross, lev


def positions_compact(
    snapshot: Dict[str, Any], marks: Dict[str, float]
) -> List[Dict[str, Any]]:
    """Return compact position list sorted by notional size."""

    out: List[Dict[str, Any]] = []
    for p in snapshot.get("positions", []):
        try:
            amt = float(p.get("positionAmt") or 0.0)
        except Exception:
            amt = 0.0
        if amt == 0:
            continue
        sym = p.get("symbol")
        side = "LONG" if amt > 0 else "SHORT"
        mark = float(marks.get(sym) or p.get("markPrice") or 0.0)
        entry = float(p.get("entryPrice") or 0.0)
        upnl = float(p.get("unrealizedProfit") or p.get("unRealizedProfit") or 0.0)
        out.append(
            {
                "symbol": sym,
                "side": side,
                "amt": amt,
                "entry": entry,
                "mark": mark,
                "upnl": upnl,
                "lev_sym": (abs(amt) * mark)
                / max(float(snapshot.get("equity", 0.0)), 1e-9),
                "positionSide": p.get("positionSide", "BOTH"),
                "isolated": bool(p.get("isolated")),
                "leverage": p.get("leverage"),
            }
        )
    out.sort(key=lambda r: abs(r["amt"] * r["mark"]), reverse=True)
    return out

