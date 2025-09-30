from __future__ import annotations

from typing import Any, Dict
import json


def _pct(x: float) -> str:
    try:
        return f"{x * 100:.2f}%"
    except Exception:
        return "-"



def _fmt_thickness(pr: float | None) -> str:
    if pr is None:
        return "—"
    if pr < 0.33:
        return "얇음"
    if pr < 0.66:
        return "보통"
    return "두꺼움"



def _ensure_dict(regime: Any) -> Dict[str, Any]:
    if isinstance(regime, dict):
        return regime
    if isinstance(regime, str):
        parts = regime.split(";")
        trend = parts[0].replace("TREND_", "") if parts else regime
        vol = parts[1].replace("VOL_", "") if len(parts) > 1 else "LOW"
        return {"code": regime, "trend": trend, "vol": vol}
    return {"code": "TREND_FLAT", "trend": "FLAT", "vol": "LOW"}


def _trace_json_like(d: Dict[str, Any]) -> str:
    keep = [
        "symbol",
        "tf",
        "score",
        "p_up",
        "stance",
        "readiness",
        "gates",
        "plan",
        "horizon_k",
    ]
    compact = {k: d[k] for k in keep if k in d}
    return json.dumps(compact, ensure_ascii=False, separators=(",", ": "))



# [ANCHOR:REPORT_IK_LINE]
def _ichimoku_line(regime_4h: Dict, f4h: Dict | None) -> str:
    ichimoku = (f4h or {}).get("ichimoku", {}) if isinstance(f4h, dict) else {}
    pos = ichimoku.get("pos_vs_cloud")
    if pos == 1:
        pos_txt = "Kumo↑"
    elif pos == -1:
        pos_txt = "Kumo↓"
    else:
        pos_txt = "Kumo內"
    thick = _fmt_thickness(ichimoku.get("cloud_thickness_pr"))
    twist_ahead = ichimoku.get("twist_ahead")
    if twist_ahead is None:
        twist_txt = ""
    elif twist_ahead <= 6:
        twist_txt = f" ⚠ twist {twist_ahead}바"
    else:
        twist_txt = f" twist {twist_ahead}바"
    return f"☁️ {pos_txt} ({thick}){twist_txt}"



# [ANCHOR:REPORT_BRIEF]
def render_brief(*args: Any, **kwargs: Any) -> str:
    """Render Discord-friendly brief for forecast outputs.

    Supports both the legacy signature ``(symbol, tf, fc, regime, feats)`` and the

    new ``(forecast_dict, regime_dict, f4h=None)`` form.
    """

    f4h = kwargs.get("f4h")
    if args and isinstance(args[0], dict):
        fx = args[0]
        regime_input = args[1] if len(args) > 1 else kwargs.get("regime_4h")
        if len(args) > 2 and f4h is None:
            f4h = args[2]
    elif len(args) >= 5:
        symbol, tf, fc, regime_input, feats = args[:5]

        fx = dict(fc)
        fx.setdefault("symbol", symbol)
        fx.setdefault("tf", tf)
        fx.setdefault("score", fc.get("score", 0.0))
        fx.setdefault("stance", fc.get("stance", "FLAT"))
        fx.setdefault("readiness", fc.get("readiness", "SCOUT"))
        fx.setdefault("p_up", fc.get("p_up", 0.5))
        fx.setdefault("horizon_k", fc.get("horizon_k", 12))
        fx.setdefault("explain", fc.get("explain", {}))
        fx.setdefault(
            "plan",
            {
                "entry": "market",
                "size_qty_est": None,
                "notional_est": None,
                "sl": feats.get("atr", 1.5) if isinstance(feats, dict) else 1.5,
                "tp_ladder": [1.0, 2.0, 3.0],
            },
        )

        if f4h is None and isinstance(feats, dict):
            f4h = feats.get("4h") if "4h" in feats else feats.get("ichimoku")

    else:
        raise TypeError("render_brief expects either (forecast, regime) or legacy signature")

    regime_dict = _ensure_dict(regime_input)
    sym = fx.get("symbol", "?")
    k = fx.get("horizon_k", 12)
    p_up = fx.get("p_up", 0.5)
    score = fx.get("score", 0.0)
    readiness = fx.get("readiness", "SCOUT")
    stance = fx.get("stance", "FLAT")

    rv_pr = regime_dict.get("rv_pr")
    if isinstance(rv_pr, (int, float)):
        rv_txt = f"RV% {int(round(rv_pr * 100))}"
    else:
        rv_txt = "RV% ?"
    code = regime_dict.get("code") or f"TREND_{regime_dict.get('trend', 'FLAT')}"
    label = regime_dict.get("label", "〰️")
    reg_line = f"4h: {code} / {rv_txt}  {label}"

    ik_line = _ichimoku_line(regime_dict, f4h)


    ex = fx.get("explain", {})
    mom = ex.get("mom", 0.0)
    brk = ex.get("breakout", 0.0)
    mr = ex.get("meanrev", 0.0)
    vol = ex.get("vol", 0.0)
    regc = ex.get("regime", 0.0)

    imk = ex.get("imk", 0.0)


    plan = fx.get("plan", {})
    entry = plan.get("entry", "market")
    size_est = plan.get("size_qty_est", "?")
    notion_est = plan.get("notional_est", "?")
    sl = plan.get("sl", 1.5)
    tp_ladder = plan.get("tp_ladder", [1.0, 2.0, 3.0])

    head = (

        f"{sym} — {reg_line}  {ik_line}\n"
        f"핵심(5m, k={k}): P(상승)={_pct(p_up)}  점수:{score:+.2f}  준비상태:{readiness}  [{stance}]"
    )
    body = (
        f"• 설명: 모멘텀 {mom:+.2f}, 돌파 {brk:+.2f}, 평균회귀 {mr:+.2f}, 변동성 {vol:+.2f}, 레짐 {regc:+.2f}, 이치모쿠 {imk:+.2f}\n"
        f"• 계획: {entry} 진입, 크기 ~{size_est} (= {notion_est}), SL {sl}×ATR, TP {','.join(str(x) for x in tp_ladder)}R\n"
        f"• 보호: BE 승격/트레일 on, 구름-게이트 적용, 쿨다운 L=5m / 15m / 1h / 4h\n"

        f"▼ trace\n{_trace_json_like(fx)}"
    )
    return head + "\n" + body

