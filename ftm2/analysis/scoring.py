# -*- coding: utf-8 -*-
"""Scoring and readiness logic"""

# [ANCHOR:SCORING]
from dataclasses import dataclass, asdict
from typing import Dict, Tuple, List
import os, math, time
from ftm2.config.aggr import load_aggr_profile


@dataclass
class ScoreDetail:
    symbol: str
    tf: str
    score: float
    direction: str        # "LONG" | "SHORT" | "FLAT"
    p_up: float
    regime: str
    ind: Dict
    contrib: Dict
    gates: Dict
    readiness: Dict
    plan: Dict
    latency_ms: int
    asof: str


def _env_floats(key: str, default: str) -> List[float]:
    return [float(x.strip()) for x in os.getenv(key, default).split(",") if x.strip()]


def _env_strs(key: str, default: str) -> List[str]:
    return [x.strip() for x in os.getenv(key, default).split(",") if x.strip()]


def _calc_direction(ema: float, ret1: float) -> str:
    if ema is None and ret1 is None:
        return "FLAT"
    mom = (ema or 0.0) + (ret1 or 0.0)
    if mom > 0:
        return "LONG"
    if mom < 0:
        return "SHORT"
    return "FLAT"


def _contrib(ema: float, rv20: float, ret1: float, rv_pr: float) -> Dict:
    # 간단한 예시 기여도 — 프로젝트에 맞게 가중치 조정 가능
    mom = (ema or 0.0) * 10000 + (ret1 or 0.0) * 10000
    meanrev = -abs(ret1 or 0.0) * 1000 * (0.5 - (rv_pr or 0.5))
    breakout = (rv20 or 0.0) * 0.0  # placeholder
    return {"momentum": mom, "meanrev": meanrev, "breakout": breakout, "carry": 0.0}


def _score_from_contrib(c: Dict) -> float:
    return (c.get("momentum", 0.0) + c.get("meanrev", 0.0) + c.get("breakout", 0.0) + c.get("carry", 0.0))



# [ADD] 정규화 유틸
def _norm_regime(regime) -> str:
    if isinstance(regime, dict):
        for k in ("code", "name", "state", "label", "value"):
            v = regime.get(k)
            if isinstance(v, str):
                return v
        return "N/A"
    if regime is None:
        return "N/A"
    return str(regime)


def _dir_from_stance(stance: str) -> str:
    if not stance:
        return "FLAT"
    s = stance.lower()
    if "long" in s or "up" in s or s in ("bull", "buy"):
        return "LONG"
    if "short" in s or "down" in s or s in ("bear", "sell"):
        return "SHORT"
    return "FLAT"



def _gate_checks(state, regime, rv_pr: float, prof: dict):
    ok, block = {}, []
    reg = _norm_regime(regime).split("_")[0].lower()
    allow = {x.lower() for x in prof["REGIME_ALLOW"]}
    reg_ok = (reg in allow) or (prof.get("ALLOW_FLAT") and reg == "flat")
    ok["regime_ok"] = reg_ok
    if not reg_ok:
        block.append("regime")

    lo, hi = prof["RV_BAND"]
    rv_ok = (rv_pr is not None) and (lo <= rv_pr <= hi)
    ok["rv_band_ok"] = rv_ok
    if not rv_ok:
        block.append("rv_band")

    risk_room = float(getattr(getattr(state, "risk", {}), "get", lambda *_: 1.0)("room", 1.0)) if hasattr(state, "risk") else 1.0
    ok["risk_ok"] = (risk_room > 0)
    ok["risk_room"] = risk_room

    cd_left = int(getattr(getattr(state, "cooldown", {}), "get", lambda *_: 0)("sec_left", 0)) if hasattr(state, "cooldown") else 0
    ok["cooldown_ok"] = (cd_left <= 0)
    ok["cooldown_s"] = max(0, cd_left)
    if cd_left > 0:
        block.append("cooldown")

    return ok, block


def _readiness(score: float, p_up: float, gates_ok: dict, blockers: list, prof: dict) -> dict:
    if not blockers and score >= prof["OPEN_TH"] and p_up >= prof["PUP_TH"]:
        return {"level": "READY", "blockers": []}
    if score >= prof["OPEN_TH"] * 0.5:
        return {"level": "CANDIDATE", "blockers": blockers}
    return {"level": "SCOUT", "blockers": blockers}


def _plan_preview(state, symbol: str, price: float) -> Dict:
    equity = max(state.monitor.get("equity") or 0.0, 1e-9)
    r_unit = float(os.getenv("RISK_R_TARGET", "0.10"))
    sl_atr = float(os.getenv("SL_ATR", "1.5"))
    tp_ladder = _env_floats("TP_LADDER", "1,2,3")
    # 매우 단순한 산출 예시 — 실제 프로젝트의 사이징 공식을 사용하세요
    notional = equity * r_unit
    size_qty = (notional / max(price, 1e-9)) if price else 0.0
    return dict(entry="market", size_qty_est=size_qty, notional_est=notional, risk_R=r_unit, sl=sl_atr, tp_ladder=tp_ladder)



def compute_score_detail(state, symbol: str, tf: str, regime, feats: Dict) -> ScoreDetail:
    # rv_pr 폴백: feats → regime.dict
    rv_pr = feats.get("rv_pr")
    if rv_pr is None and isinstance(regime, dict):
        rv_pr = regime.get("rv_pr") or regime.get("rvp") or regime.get("rv_pctile")

    ema, rv20, atr, ret1 = feats.get("ema"), feats.get("rv20"), feats.get("atr"), feats.get("ret1")

    direction = _calc_direction(ema, ret1)
    contrib = _contrib(ema, rv20, ret1, rv_pr)
    score = _score_from_contrib(contrib)
    # p_up 간이 캘리브레이션: 스코어 시그넘 기반
    p_up = 0.50 + 0.10 * (1 if score > 0 else (-1 if score < 0 else 0))

    # --- Forecast 오버라이드(있으면 최우선 사용)
    fc = None
    if hasattr(state, "forecasts"):
        fc = state.forecasts.get((symbol, tf)) or state.forecasts.get(symbol, {}).get(tf)
    if fc:
        if "score" in fc:
            try:
                score = float(fc["score"]) * 100.0
            except Exception:
                pass
        if "p_up" in fc:
            try:
                p_up = float(fc["p_up"])
            except Exception:
                pass
        if "stance" in fc:
            direction = _dir_from_stance(fc["stance"])
        try:
            contrib["forecast"] = round(float(fc.get("score", 0.0)) * 100.0, 2)
        except Exception:
            contrib["forecast"] = 0.0

    prof = load_aggr_profile(state)
    gates_ok, blockers = _gate_checks(state, regime, rv_pr, prof)
    ready = _readiness(score, p_up, gates_ok, blockers, prof)
    price = state.marks.get(symbol)
    plan = _plan_preview(state, symbol, price)

    regime_str = _norm_regime(regime)

    return ScoreDetail(
        symbol=symbol,
        tf=tf,
        score=round(score, 2),
        direction=direction,
        p_up=round(p_up, 2),
        regime=regime_str,

        ind=dict(ema=ema, rv20=rv20, atr=atr, ret1=ret1, rv_pr=rv_pr),
        contrib={k: round(v, 2) for k, v in contrib.items()},
        gates=gates_ok,
        readiness=ready,
        plan=plan,
        latency_ms=int(state.latency_ms(symbol) if hasattr(state, "latency_ms") else 0),
        asof=feats.get("asof") or state.now_iso(),
    )


def compute_multi_tf(state, symbol: str) -> List[ScoreDetail]:
    tfs = _env_strs("TF_ORDER", "5m,15m,1h,4h")
    out = []
    for tf in tfs:
        feats = state.compute_features(symbol, tf) if hasattr(state, "compute_features") else {}
        if not feats:
            feats = state.try_features(symbol, tf) if hasattr(state, "try_features") else {}
        if not feats:
            feats = {}
        regime = state.regime.get(symbol, {}).get(tf) if hasattr(state, "regime") else "N/A"
        out.append(compute_score_detail(state, symbol, tf, regime, feats))
    return out
# [ANCHOR:SCORING] end
