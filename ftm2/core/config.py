# -*- coding: utf-8 -*-
"""
ENV/DB 설정 로더
- 우선순위: DB(config 테이블) > os.environ > 기본값
"""
from __future__ import annotations
import os
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

try:
    from ftm2.signal.forecast import ForecastConfig
except Exception:  # pragma: no cover
    from signal.forecast import ForecastConfig  # type: ignore

# [ANCHOR:CFG_LOADER]
def _get_db(cfg_db, key: str) -> Optional[str]:
    try:
        return cfg_db.get_config(key)  # Persistence.get_config
    except Exception:
        return None

def _get_env(key: str) -> Optional[str]:
    v = os.getenv(key)
    return v if v not in (None, "") else None

def _as_float(v: Optional[str], default: float) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default

def _as_tuple3(v: Optional[str], default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    if not v:
        return default
    try:
        parts = [p.strip() for p in v.split(",")]
        a, b, c = float(parts[0]), float(parts[1]), float(parts[2])
        return (a, b, c)
    except Exception:
        return default

def load_forecast_cfg(cfg_db) -> ForecastConfig:
    """
    ENV 키:
      FC_STRONG_THR, FC_FLAT_THR, FC_SPREAD_SCALE, FC_MR_CENTER, FC_MR_SCALE,
      FC_LAMBDA_PERF, FC_W_CLIP_LO, FC_W_CLIP_HI,
      FC_WEIGHTS_TREND_UP, FC_WEIGHTS_TREND_DOWN, FC_WEIGHTS_RANGE_HIGH, FC_WEIGHTS_RANGE_LOW
      (예: FC_WEIGHTS_TREND_UP="0.6,0.1,0.3")
    DB 키:
      forecast.strong_thr, forecast.flat_thr, forecast.spread_scale, forecast.mr_center, forecast.mr_scale,
      forecast.lambda_perf, forecast.w_clip_lo, forecast.w_clip_hi,
      forecast.weights.TREND_UP, forecast.weights.TREND_DOWN, forecast.weights.RANGE_HIGH, forecast.weights.RANGE_LOW
    """
    base = ForecastConfig()  # 기본값

    strong = _as_float(_get_db(cfg_db, "forecast.strong_thr") or _get_env("FC_STRONG_THR"), base.strong_thr)
    flat   = _as_float(_get_db(cfg_db, "forecast.flat_thr")   or _get_env("FC_FLAT_THR"),   base.flat_thr)

    spread = _as_float(_get_db(cfg_db, "forecast.spread_scale") or _get_env("FC_SPREAD_SCALE"), base.spread_scale)
    mr_ctr = _as_float(_get_db(cfg_db, "forecast.mr_center")    or _get_env("FC_MR_CENTER"),    base.mr_center)
    mr_scl = _as_float(_get_db(cfg_db, "forecast.mr_scale")     or _get_env("FC_MR_SCALE"),     base.mr_scale)

    lam    = _as_float(_get_db(cfg_db, "forecast.lambda_perf")  or _get_env("FC_LAMBDA_PERF"),  base.lambda_perf)
    w_lo   = _as_float(_get_db(cfg_db, "forecast.w_clip_lo")    or _get_env("FC_W_CLIP_LO"),    base.w_clip_lo)
    w_hi   = _as_float(_get_db(cfg_db, "forecast.w_clip_hi")    or _get_env("FC_W_CLIP_HI"),    base.w_clip_hi)

    w_up   = _as_tuple3(_get_db(cfg_db, "forecast.weights.TREND_UP")   or _get_env("FC_WEIGHTS_TREND_UP"),   base.base_weights["TREND_UP"])
    w_dn   = _as_tuple3(_get_db(cfg_db, "forecast.weights.TREND_DOWN") or _get_env("FC_WEIGHTS_TREND_DOWN"), base.base_weights["TREND_DOWN"])
    w_rh   = _as_tuple3(_get_db(cfg_db, "forecast.weights.RANGE_HIGH") or _get_env("FC_WEIGHTS_RANGE_HIGH"), base.base_weights["RANGE_HIGH"])
    w_rl   = _as_tuple3(_get_db(cfg_db, "forecast.weights.RANGE_LOW")  or _get_env("FC_WEIGHTS_RANGE_LOW"),  base.base_weights["RANGE_LOW"])

    cfg = ForecastConfig(
        spread_scale=spread,
        mr_center=mr_ctr,
        mr_scale=mr_scl,
        strong_thr=strong,
        flat_thr=flat,
        lambda_perf=lam,
        w_clip_lo=w_lo,
        w_clip_hi=w_hi,
        base_weights={
            "TREND_UP": w_up,
            "TREND_DOWN": w_dn,
            "RANGE_HIGH": w_rh,
            "RANGE_LOW": w_rl,
        },
    )
    return cfg


@dataclass
class _RiskCfgView:
    risk_target_pct: float
    corr_cap_per_side: float
    day_max_loss_pct: float
    atr_k: float
    min_notional: float
    equity_override: Optional[float]


def load_risk_cfg(cfg_db) -> _RiskCfgView:
    """
    ENV 키:
      RISK_TARGET_PCT, CORR_CAP_PER_SIDE, DAILY_MAX_LOSS_PCT, RISK_ATR_K,
      RISK_MIN_NOTIONAL, RISK_EQUITY_OVERRIDE
    DB 키:
      risk.target_pct, risk.corr_cap_per_side, risk.day_max_loss_pct,
      risk.atr_k, risk.min_notional, risk.equity_override
    """

    def g_db(k: str) -> Optional[str]:
        try:
            return cfg_db.get_config(k)
        except Exception:  # pragma: no cover - db access failures
            return None

    def g_env(k: str) -> Optional[str]:
        v = os.getenv(k)
        return v if v not in (None, "") else None

    def f(v: Optional[str], d: float) -> float:
        try:
            return float(v) if v is not None else d
        except Exception:
            return d

    rt = f(g_db("risk.target_pct") or g_env("RISK_TARGET_PCT"), 0.30)
    cc = f(g_db("risk.corr_cap_per_side") or g_env("CORR_CAP_PER_SIDE"), 0.65)
    dl = f(g_db("risk.day_max_loss_pct") or g_env("DAILY_MAX_LOSS_PCT"), 3.0)
    ak = f(g_db("risk.atr_k") or g_env("RISK_ATR_K"), 2.0)
    mn = f(g_db("risk.min_notional") or g_env("RISK_MIN_NOTIONAL"), 20.0)
    eo = g_db("risk.equity_override") or g_env("RISK_EQUITY_OVERRIDE")
    eo_f = float(eo) if eo not in (None, "") else None

    return _RiskCfgView(
        risk_target_pct=rt,
        corr_cap_per_side=cc,
        day_max_loss_pct=dl,
        atr_k=ak,
        min_notional=mn,
        equity_override=eo_f,
    )
