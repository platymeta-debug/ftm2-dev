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


@dataclass
class _ExecCfgView:
    active: bool
    cooldown_s: float
    tol_rel: float
    tol_abs: float
    order_type: str
    reduce_only: bool


def _get_db_val(cfg_db, key: str) -> Optional[str]:
    try:
        return cfg_db.get_config(key)
    except Exception:
        return None


def _get_env_val(key: str) -> Optional[str]:
    import os
    v = os.getenv(key)
    return v if v not in (None, "") else None


def _as_bool(v: Optional[str], default: bool) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _as_float(v: Optional[str], default: float) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def load_exec_cfg(cfg_db) -> _ExecCfgView:
    """
    ENV:
      EXEC_ACTIVE, EXEC_COOLDOWN_S, EXEC_TOL_REL, EXEC_TOL_ABS, EXEC_ORDER_TYPE, EXEC_REDUCE_ONLY
    DB:
      exec.active, exec.cooldown_s, exec.tol_rel, exec.tol_abs, exec.order_type, exec.reduce_only
    """

    A = _get_db_val(cfg_db, "exec.active") or _get_env_val("EXEC_ACTIVE")
    CD = _get_db_val(cfg_db, "exec.cooldown_s") or _get_env_val("EXEC_COOLDOWN_S")
    TR = _get_db_val(cfg_db, "exec.tol_rel") or _get_env_val("EXEC_TOL_REL")
    TA = _get_db_val(cfg_db, "exec.tol_abs") or _get_env_val("EXEC_TOL_ABS")
    OT = _get_db_val(cfg_db, "exec.order_type") or _get_env_val("EXEC_ORDER_TYPE")
    RO = _get_db_val(cfg_db, "exec.reduce_only") or _get_env_val("EXEC_REDUCE_ONLY")

    return _ExecCfgView(
        active=_as_bool(A, False),
        cooldown_s=_as_float(CD, 5.0),
        tol_rel=_as_float(TR, 0.05),
        tol_abs=_as_float(TA, 0.0),
        order_type=(OT or "MARKET"),
        reduce_only=_as_bool(RO, True),
    )


@dataclass
class _ProtectCfgView:
    slip_warn_pct: float
    slip_max_pct: float
    stale_rel: float
    stale_secs: float
    eps_rel: float
    eps_abs: float
    partial_timeout_s: float
    cancel_on_stale: bool



def load_protect_cfg(cfg_db) -> _ProtectCfgView:
    """
    ENV: PROT_SLIP_WARN_PCT, PROT_SLIP_MAX_PCT, PROT_STALE_REL, PROT_STALE_SECS,
         PROT_EPS_REL, PROT_EPS_ABS, PROT_PARTIAL_TIMEOUT_S, PROT_CANCEL_ON_STALE
    DB : protect.slip_warn_pct, protect.slip_max_pct, protect.stale_rel, protect.stale_secs,
         protect.eps_rel, protect.eps_abs, protect.partial_timeout_s, protect.cancel_on_stale

    """

    def gdb(k):
        try:
            return cfg_db.get_config(k)
        except Exception:
            return None

    def genv(k):
        import os
        v = os.getenv(k)
        return v if v not in (None, "") else None

    def f(v, d):
        try:
            return float(v) if v is not None else d
        except Exception:
            return d

    def b(v, d):
        if v is None:
            return d
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


    return _ProtectCfgView(
        slip_warn_pct=f(gdb("protect.slip_warn_pct") or genv("PROT_SLIP_WARN_PCT"), 0.003),
        slip_max_pct=f(gdb("protect.slip_max_pct") or genv("PROT_SLIP_MAX_PCT"), 0.008),
        stale_rel=f(gdb("protect.stale_rel") or genv("PROT_STALE_REL"), 0.5),
        stale_secs=f(gdb("protect.stale_secs") or genv("PROT_STALE_SECS"), 20.0),
        eps_rel=f(gdb("protect.eps_rel") or genv("PROT_EPS_REL"), 0.10),
        eps_abs=f(gdb("protect.eps_abs") or genv("PROT_EPS_ABS"), 0.0001),
        partial_timeout_s=f(gdb("protect.partial_timeout_s") or genv("PROT_PARTIAL_TIMEOUT_S"), 45.0),
        cancel_on_stale=b(gdb("protect.cancel_on_stale") or genv("PROT_CANCEL_ON_STALE"), True),
    )



@dataclass
class _OOCfgView:
    enabled: bool
    poll_s: float
    stale_secs: float
    price_drift_pct: float
    cancel_on_day_cut: bool
    max_open_per_sym: int


def load_open_orders_cfg(cfg_db) -> _OOCfgView:
    """
    ENV: OO_ENABLED, OO_POLL_S, OO_STALE_SECS, OO_PRICE_DRIFT_PCT, OO_CANCEL_ON_DAY_CUT, OO_MAX_OPEN_PER_SYM
    DB : oo.enabled, oo.poll_s, oo.stale_secs, oo.price_drift_pct, oo.cancel_on_day_cut, oo.max_open_per_sym
    """
    def gdb(k):
        try:
            return cfg_db.get_config(k)
        except Exception:
            return None
    def genv(k):
        import os
        v = os.getenv(k)
        return v if v not in (None, "") else None
    def f(v, d):
        try:
            return float(v) if v is not None else d
        except Exception:
            return d
    def i(v, d):
        try:
            return int(float(v)) if v is not None else d
        except Exception:
            return d
    def b(v, d):
        if v is None:
            return d
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on")
    return _OOCfgView(
        enabled=b(gdb("oo.enabled") or genv("OO_ENABLED"), True),
        poll_s=f(gdb("oo.poll_s") or genv("OO_POLL_S"), 3.0),
        stale_secs=f(gdb("oo.stale_secs") or genv("OO_STALE_SECS"), 45.0),
        price_drift_pct=f(gdb("oo.price_drift_pct") or genv("OO_PRICE_DRIFT_PCT"), 0.004),
        cancel_on_day_cut=b(gdb("oo.cancel_on_day_cut") or genv("OO_CANCEL_ON_DAY_CUT"), True),
        max_open_per_sym=i(gdb("oo.max_open_per_sym") or genv("OO_MAX_OPEN_PER_SYM"), 2),
    )



@dataclass
class _GuardCfgView:
    enabled: bool
    max_lever_total: float
    max_lever_per_sym: float
    stop_pct: float
    trail_activate_pct: float
    trail_width_pct: float


def load_guard_cfg(cfg_db) -> _GuardCfgView:
    """
    ENV: GUARD_ENABLED, GUARD_MAX_LEVER, GUARD_MAX_LEVER_PER_SYM, GUARD_STOP_PCT,
         GUARD_TRAIL_ACTIVATE_PCT, GUARD_TRAIL_WIDTH_PCT
    DB : guard.enabled, guard.max_lever, guard.max_lever_per_sym, guard.stop_pct,
         guard.trail_activate_pct, guard.trail_width_pct
    """

    def gdb(k):
        try:
            return cfg_db.get_config(k)
        except Exception:
            return None

    def genv(k):
        import os
        v = os.getenv(k)
        return v if v not in (None, "") else None

    def f(v, d):
        try:
            return float(v) if v is not None else d
        except Exception:
            return d

    def b(v, d):
        if v is None:
            return d
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

    return _GuardCfgView(
        enabled=b(gdb("guard.enabled") or genv("GUARD_ENABLED"), True),
        max_lever_total=f(gdb("guard.max_lever") or genv("GUARD_MAX_LEVER"), 2.5),
        max_lever_per_sym=f(gdb("guard.max_lever_per_sym") or genv("GUARD_MAX_LEVER_PER_SYM"), 0.8),
        stop_pct=f(gdb("guard.stop_pct") or genv("GUARD_STOP_PCT"), 3.0),
        trail_activate_pct=f(gdb("guard.trail_activate_pct") or genv("GUARD_TRAIL_ACTIVATE_PCT"), 1.0),
        trail_width_pct=f(gdb("guard.trail_width_pct") or genv("GUARD_TRAIL_WIDTH_PCT"), 0.6),
    )



@dataclass
class _ExecQCfgView:
    window_sec: int
    alert_p90_bps: float
    min_fills: int
    report_sec: int


def load_execq_cfg(cfg_db) -> _ExecQCfgView:
    """
    ENV: EQ_WINDOW_SEC, EQ_ALERT_P90_BPS, EQ_MIN_FILLS, EQ_REPORT_SEC
    DB : eq.window_sec, eq.alert_p90_bps, eq.min_fills, eq.report_sec
    """

    def gdb(k):
        try:
            return cfg_db.get_config(k)
        except Exception:
            return None

    def genv(k):
        import os
        v = os.getenv(k)
        return v if v not in (None, "") else None

    def i(v, d):
        try:
            return int(float(v)) if v is not None else d
        except Exception:
            return d

    def f(v, d):
        try:
            return float(v) if v is not None else d
        except Exception:
            return d

    return _ExecQCfgView(
        window_sec=i(gdb("eq.window_sec") or genv("EQ_WINDOW_SEC"), 600),
        alert_p90_bps=f(gdb("eq.alert_p90_bps") or genv("EQ_ALERT_P90_BPS"), 8.0),
        min_fills=i(gdb("eq.min_fills") or genv("EQ_MIN_FILLS"), 5),
        report_sec=i(gdb("eq.report_sec") or genv("EQ_REPORT_SEC"), 30),
    )


@dataclass
class _OLCfgView:
    window_sec: int
    report_sec: int
    min_orders: int


def load_order_ledger_cfg(cfg_db) -> _OLCfgView:
    """
    ENV: OL_WINDOW_SEC, OL_REPORT_SEC, OL_MIN_ORDERS
    DB : ol.window_sec, ol.report_sec, ol.min_orders
    """

    def gdb(k):
        try:
            return cfg_db.get_config(k)
        except Exception:
            return None

    def genv(k):
        import os
        v = os.getenv(k)
        return v if v not in (None, "") else None

    def i(v, d):
        try:
            return int(float(v)) if v is not None else d
        except Exception:
            return d

    return _OLCfgView(
        window_sec=i(gdb("ol.window_sec") or genv("OL_WINDOW_SEC"), 600),
        report_sec=i(gdb("ol.report_sec") or genv("OL_REPORT_SEC"), 60),
        min_orders=i(gdb("ol.min_orders") or genv("OL_MIN_ORDERS"), 5),
    )


@dataclass
class _KPICfgView:
    enabled: bool
    report_sec: int
    to_discord: bool
    only_on_change: bool

def load_kpi_cfg(cfg_db) -> _KPICfgView:
    """
    ENV: KPI_ENABLED, KPI_REPORT_SEC, KPI_TO_DISCORD, KPI_ONLY_ON_CHANGE
    DB : kpi.enabled, kpi.report_sec, kpi.to_discord, kpi.only_on_change
    """
    def gdb(k):
        try: return cfg_db.get_config(k)
        except Exception: return None
    def genv(k):
        import os
        v = os.getenv(k); return v if v not in (None, "") else None
    def b(v, d):
        if v is None: return d
        return str(v).strip().lower() in ("1","true","yes","y","on")
    def i(v, d):
        try: return int(float(v)) if v is not None else d
        except Exception: return d

    return _KPICfgView(
        enabled=b(gdb("kpi.enabled")      or genv("KPI_ENABLED"),       True),
        report_sec=i(gdb("kpi.report_sec") or genv("KPI_REPORT_SEC"),   30),
        to_discord=b(gdb("kpi.to_discord") or genv("KPI_TO_DISCORD"),   True),
        only_on_change=b(gdb("kpi.only_on_change") or genv("KPI_ONLY_ON_CHANGE"), True),
    )

