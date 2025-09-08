# -*- coding: utf-8 -*-
"""Human friendly analysis report renderer"""

# [ANCHOR:ANALYSIS_REPORT]
import json
from typing import Dict, List


def _fmt_pct(x):
    if x is None or x == "—":
        return "—"
    try:
        return f"{float(x):.2%}"
    except Exception:
        return "—"


def _fmt(v, digits=5):
    if v is None or v == "—":
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "—"


def _status_emoji(level: str) -> str:
    return {"READY": "✅", "CANDIDATE": "🟡", "SCOUT": "🩶"}.get(level, "🩶")


def _norm_regime_txt(reg):
    if isinstance(reg, dict):
        for k in ("code", "name", "state", "label", "value"):
            v = reg.get(k)
            if isinstance(v, str):
                return v
        return "N/A"
    if reg is None:
        return "N/A"
    return str(reg)


def render_analysis_message(state, details_by_symbol: Dict[str, List]) -> str:
    parts = []
    parts.append(f"🧠 실시간 분석 리포트 v2 ({state.now_iso_utc()})  ※ 데이터: live · 트레이딩: {state.trade_mode}")
    for sym, details in details_by_symbol.items():
        # 티켓 후보
        from ftm2.analysis.ticket import synthesize_ticket
        ticket = synthesize_ticket(details)
        # 요약 줄
        best = max(details, key=lambda d: (d.readiness.get('level')=="READY", d.score, d.p_up))
        emoji = _status_emoji(best.readiness.get("level"))
        parts.append("")
        parts.append(f"{sym} — {emoji} {best.readiness.get('level')} {best.direction} {best.score:+.2f} (p_up {best.p_up:.2f})")
        # 사유/지표
        c = best.contrib; ind = best.ind; gates = best.gates
        reg_txt = _norm_regime_txt(best.regime)
        rvp = ind.get("rv_pr")
        rvp_txt = "—" if rvp is None else f"{float(rvp):.3f}"
        parts.append(
            f"• 이유: 모멘텀 {c.get('momentum',0):+.2f}, 돌파 {c.get('breakout',0):+.2f}, 평균회귀 {c.get('meanrev',0):+.2f} | 레짐 {reg_txt}, RV%tile {rvp_txt} {'✅' if all([gates.get('regime_ok'),gates.get('rv_band_ok')]) else '⚠️'}"
        )
        # 계획/안전장치
        plan = best.plan
        parts.append(f"• 계획: {plan.get('entry','?')} 진입, 크기 ~{plan.get('size_qty_est',0):.6f} {sym[:-4]}(≈${plan.get('notional_est',0):,.0f}, {plan.get('risk_R',0):.2f}R), SL {plan.get('sl',0):.2f}×ATR, TP {','.join(str(x) for x in plan.get('tp_ladder',[]))}R")
        parts.append(f"• 안전장치: regime_ok={gates.get('regime_ok')} rv_band_ok={gates.get('rv_band_ok')} risk_ok={gates.get('risk_ok')} cooldown_ok={gates.get('cooldown_ok')}")
        # TF 흐름
        from ftm2.analysis.ticket import _vote
        vt = _vote(details)
        parts.append(f"• 신호흐름: {vt['flow']}  (가중합 L={vt['long']} / S={vt['short']})")
        # 보류 사유
        if best.readiness.get("level") != "READY":
            blocks = best.readiness.get("blockers", [])
            if blocks:
                parts.append(f"• 보류: {', '.join(blocks)}")
        # trace (접이식이 불가하니 한 줄 요약 + JSON 블럭)
        compact = dict(symbol=sym, readiness=best.readiness.get("level"), score=best.score, gates=best.gates, plan=best.plan)
        parts.append("▼ trace")
        parts.append("```json\n" + json.dumps(compact, ensure_ascii=False) + "\n```")
    return "\n".join(parts)
# [ANCHOR:ANALYSIS_REPORT] end
