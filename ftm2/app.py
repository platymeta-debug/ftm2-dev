# -*- coding: utf-8 -*-
"""
FTM2 Orchestrator (minimal)
- ENV 로딩 → BinanceClient 생성 → WS 스트림(StreamManager) 시작
- 10초 하트비트 로그
- Ctrl+C 안전 종료
"""
from __future__ import annotations
try:
    from ftm2.core.logging import setup_logging
    setup_logging()
except Exception:
    pass

try:
    from ftm2.ops.sentry_init import init_sentry
    init_sentry()
except Exception:
    pass


import os
import time
import signal
import logging
import threading
from typing import List

# [ANCHOR:STRAT_ROUTE] begin
def _exec_active_from_env() -> bool:
    v = os.getenv("EXEC_ACTIVE", "0").strip()
    return v in ("1", "true", "True", "YES", "yes")


def is_exec_enabled(bus) -> bool:
    try:
        return bool(getattr(getattr(bus, "config", object()), "exec_active", _exec_active_from_env()))
    except Exception:
        return _exec_active_from_env()
# [ANCHOR:STRAT_ROUTE] end

# 글로벌 루프 가드 및 유틸
_EQUITY_LOOP_STARTED = False
_equity_lock = threading.Lock()
_HEARTBEAT_STARTED = False
_heartbeat_lock = threading.Lock()


def _clamped_interval(env_key: str, default_s: int, min_s: int = 5) -> float:
    try:
        v = int(os.getenv(env_key, str(default_s)))
    except Exception:
        v = default_s
    return max(min_s, v)


def _sleep_with_jitter(base_s: float) -> None:
    import random, time

    time.sleep(base_s + random.random() * (base_s * 0.1))

# 로컬 모듈
try:
    from ftm2.core.env import load_env_chain
    from ftm2.core.state import StateBus
except Exception:  # pragma: no cover
    from core.env import load_env_chain  # type: ignore
    from core.state import StateBus  # type: ignore

try:
    from ftm2.core.config import load_modes_cfg
    from ftm2.exchange.binance import BinanceClient, get_klines
    from ftm2.data.streams import StreamManager
except Exception:  # pragma: no cover
    from core.config import load_modes_cfg  # type: ignore
    from exchange.binance import BinanceClient, get_klines  # type: ignore
    from data.streams import StreamManager  # type: ignore

try:
    from ftm2.core.persistence import Persistence
except Exception:  # pragma: no cover
    from core.persistence import Persistence  # type: ignore

try:
    from ftm2.discord_bot.bot import run_discord_bot
except Exception:  # pragma: no cover
    from discord_bot.bot import run_discord_bot  # type: ignore

try:
    from ftm2.signal.dummy import DummyForecaster
except Exception:  # pragma: no cover
    from signal.dummy import DummyForecaster  # type: ignore

try:
    from ftm2.signal.regime import RegimeClassifier, RegimeConfig
except Exception:  # pragma: no cover
    from signal.regime import RegimeClassifier, RegimeConfig  # type: ignore

try:
    from ftm2.data.features import FeatureEngine, FeatureConfig
except Exception:  # pragma: no cover
    from data.features import FeatureEngine, FeatureConfig  # type: ignore

try:
    from ftm2.signal.forecast import ForecastEnsemble, ForecastConfig
except Exception:  # pragma: no cover
    from signal.forecast import ForecastEnsemble, ForecastConfig  # type: ignore

try:
    from ftm2.trade.risk import RiskEngine, RiskConfig
    from ftm2.core.config import load_forecast_cfg, load_risk_cfg
except Exception:  # pragma: no cover
    from trade.risk import RiskEngine, RiskConfig  # type: ignore
    from core.config import load_forecast_cfg, load_risk_cfg  # type: ignore

try:
    from ftm2.utils.env import env_str, env_list, env_int, env_bool
except Exception:  # pragma: no cover
    from utils.env import env_str, env_list, env_int, env_bool  # type: ignore

try:
    from ftm2.trade.router import OrderRouter, ExecConfig
    from ftm2.core.config import load_exec_cfg
except Exception:  # pragma: no cover
    from trade.router import OrderRouter, ExecConfig  # type: ignore
    from core.config import load_exec_cfg  # type: ignore

try:
    from ftm2.trade.reconcile import Reconciler, ProtectConfig
    from ftm2.core.config import load_protect_cfg
except Exception:  # pragma: no cover
    from trade.reconcile import Reconciler, ProtectConfig  # type: ignore
    from core.config import load_protect_cfg  # type: ignore

try:
    from ftm2.trade.open_orders import OpenOrdersManager, OOConfig
    from ftm2.core.config import load_open_orders_cfg
except Exception:  # pragma: no cover
    from trade.open_orders import OpenOrdersManager, OOConfig  # type: ignore
    from core.config import load_open_orders_cfg  # type: ignore

try:
    from ftm2.trade.guard import PositionGuard, GuardConfig
    from ftm2.core.config import load_guard_cfg
except Exception:  # pragma: no cover
    from trade.guard import PositionGuard, GuardConfig  # type: ignore
    from core.config import load_guard_cfg  # type: ignore

try:
    from ftm2.metrics.exec_quality import get_exec_quality, ExecQConfig
    from ftm2.core.config import load_execq_cfg
except Exception:  # pragma: no cover
    from metrics.exec_quality import get_exec_quality, ExecQConfig  # type: ignore
    from core.config import load_execq_cfg  # type: ignore

try:
    from ftm2.metrics.order_ledger import get_order_ledger, OLConfig
    from ftm2.core.config import load_order_ledger_cfg
except Exception:  # pragma: no cover
    from metrics.order_ledger import get_order_ledger, OLConfig  # type: ignore
    from core.config import load_order_ledger_cfg  # type: ignore

try:
    from ftm2.monitor.kpi import compute_kpi_snapshot
    from ftm2.core.config import load_kpi_cfg
    from ftm2.discord.panel import render_kpi_message
    from ftm2.discord_bot.notify import enqueue_alert
except Exception:  # pragma: no cover
    from monitor.kpi import compute_kpi_snapshot  # type: ignore
    from core.config import load_kpi_cfg  # type: ignore
    from discord.panel import render_kpi_message  # type: ignore
    from discord_bot.notify import enqueue_alert  # type: ignore

try:
    from ftm2.ops.http import OpsHttp, OpsHttpConfig
    from ftm2.core.config import load_ops_http_cfg
except Exception:  # pragma: no cover
    from ops.http import OpsHttp, OpsHttpConfig  # type: ignore
    from core.config import load_ops_http_cfg  # type: ignore

try:
    from ftm2.replay.engine import ReplayEngine, ReplayConfig
    from ftm2.core.config import load_replay_cfg
except Exception:  # pragma: no cover
    from replay.engine import ReplayEngine, ReplayConfig  # type: ignore
    from core.config import load_replay_cfg  # type: ignore

log = logging.getLogger("ftm2.orch")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# [ANCHOR:KEY_SELECT] begin
def _mask(s: str | None, keep: int = 4) -> str:
    if not s:
        return ""
    s = str(s)
    return s[:keep] + "*" * max(0, len(s) - keep - 2) + s[-2:]


def _pick_keys(trade_mode: str):
    if (trade_mode or "").lower() == "live":
        k = os.getenv("BINANCE_API_KEY")
        s = os.getenv("BINANCE_API_SECRET")
        scope = "live"
    else:
        k = os.getenv("BINANCE_TESTNET_API_KEY")
        s = os.getenv("BINANCE_TESTNET_API_SECRET")
        scope = "testnet"
    return scope, k, s


def init_account_bus(bus) -> None:
    import logging

    log = logging.getLogger("ftm2.orch")
    tm = os.getenv("TRADE_MODE", "testnet")
    scope, k, s = _pick_keys(tm)

    use_user = env_bool("USE_USER", False)
    if not use_user:
        log.info("[ACCOUNT] user stream disabled (USE_USER=0)")
        return

    if not k or not s:
        log.warning("E_BAL_POLL_FAIL scope=%s reason=NO_API_KEY", scope)
        return

    log.info("[ACCOUNT] scope=%s key=%s secret=%s", scope, _mask(k), _mask(s))

    # 계정/포지션 초기화
    try:
        cli = BinanceClient(scope, k or "", s or "", order_active=False)
        try:
            cli.sync_time()
        except Exception:
            pass


        snap = cli.account_snapshot()
        if snap:
            bus.set_account({
                "ccy": "USDT",
                "totalWalletBalance": snap.get("wallet", 0.0),
                "availableBalance": snap.get("avail", 0.0),
                "totalUnrealizedProfit": snap.get("upnl", 0.0),
                "totalMarginBalance": snap.get("equity", 0.0),
                "wallet": snap.get("wallet", 0.0),
                "avail": snap.get("avail", 0.0),
                "upnl": snap.get("upnl", 0.0),
                "equity": snap.get("equity", 0.0),
            })
            log.info(
                "[ACCOUNT_INIT] wallet=%.2f equity=%.2f upnl=%.2f avail=%.2f",
                snap.get("wallet", 0.0),
                snap.get("equity", 0.0),
                snap.get("upnl", 0.0),
                snap.get("avail", 0.0),

            )

        syms = env_list("SYMBOLS") or []
        if syms:
            pos = cli.fetch_positions(syms)
            if pos:
                bus.set_positions(pos)
                log.info("[ACCOUNT_INIT] positions loaded n=%d", len(pos))
    except Exception as e:
        log.warning("[ACCOUNT_INIT] failed: %s", e)
# [ANCHOR:KEY_SELECT] end


# [ANCHOR:EQUITY_SOURCE] begin
def resolve_equity(bus) -> float:
    """계정 폴링 값이 있으면 최우선, 그다음 OVERRIDE, 없으면 기본 1000."""
    acct_eq = None
    try:
        acct_eq = getattr(getattr(bus, "state", object()), "equity_usdt", None)
    except Exception:
        acct_eq = None
    if isinstance(acct_eq, (int, float)) and acct_eq > 0:
        return float(acct_eq)

    ov = os.getenv("RISK_EQUITY_OVERRIDE", "").strip()
    if ov:
        try:
            return float(ov)
        except ValueError:
            log.warning("E_EQUITY_OVERRIDE_PARSE val=%r", ov)

    return 1000.0
# [ANCHOR:EQUITY_SOURCE] end

# [ANCHOR:ORCH_EQUITY_LOOP]
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _safe_tz():
    import os
    name = os.getenv("DAY_PNL_TZ", "Asia/Seoul")
    try:
        return ZoneInfo(name)
    except Exception:
        # Windows 등 tzdata 미설치 환경 폴백
        return timezone.utc


def _kst_today_str():
    tz = _safe_tz()
    return datetime.now(tz).strftime("%Y-%m-%d")


def _get_day_e0(state):
    return state.config.get(f"day_e0_{_kst_today_str()}")


def _set_day_e0(state, equity: float):
    key = f"day_e0_{_kst_today_str()}"
    state.config[key] = equity
    try:
        state.db.upsert_config(key, str(equity))
        state.log.info(f"[DAY_E0] {_kst_today_str()} KST equity_open={equity:.2f}")
    except Exception as e:
        state.log.warning(f"[DAY_E0][WARN] persist failed: {e}")


def equity_heartbeat_loop(state):
    snap = state.snapshot()
    equity = (snap.get("account") or {}).get("totalMarginBalance")
    if equity is None:
        return
    if _get_day_e0(state) is None:
        _set_day_e0(state, equity)
    mon = snap.get("monitor") or {}
    mon["equity"] = equity
    state.set_monitor_state(mon)

# [ANCHOR:ORCH]
class Orchestrator:
    def __init__(self) -> None:
        self.env = load_env_chain()
        self.mode = env_str("MODE", "testnet").lower()
        self.symbols: List[str] = env_list("SYMBOLS") or ["BTCUSDT", "ETHUSDT"]
        self.tf_exec = env_str("TF_EXEC", "1m")
        self.kline_intervals = env_list("TF_SIGNAL") or ["5m", "15m", "1h", "4h"]
        self.eval_interval = self.kline_intervals[0] if self.kline_intervals else "5m"
        self.regime_interval = self.kline_intervals[0] if self.kline_intervals else "5m"
        self.bus = StateBus()
        init_account_bus(self.bus)
        self.db_path = os.getenv("DB_PATH") or "./runtime/trader.db"
        self.db = Persistence(self.db_path)
        self.db.ensure_schema()
        try:
            self.db.record_event("INFO", "system", "boot")
        except Exception:
            pass

        self.bus.config = {}
        self.bus.db = self.db
        self.bus.log = log

        modes = load_modes_cfg(self.db)
        exv = load_exec_cfg(self.db)
        # [ANCHOR:DUAL_MODE]
        # 시세 스트림은 항상 라이브 사용
        self.cli_data = BinanceClient.for_data("live")
        self.cli_trade = BinanceClient.for_trade(modes.trade_mode, order_active=exv.active)
        try:
            self.cli_trade.warmup_filters(self.symbols)
        except Exception:
            pass
        self.streams = StreamManager(
            self.cli_data,
            None if modes.trade_mode == "dry" else self.cli_trade,
            self.bus,
            self.symbols,
            self.kline_intervals,
            use_mark=True,
            use_user=True,
        )

        self.forecaster = DummyForecaster(self.symbols, self.eval_interval)
        self.feature_engine = FeatureEngine(self.symbols, self.kline_intervals, FeatureConfig())
        self.regime = RegimeClassifier(self.symbols, self.regime_interval, RegimeConfig())
        self.forecast_interval = getattr(self, "regime_interval", None) or (
            self.kline_intervals[0] if self.kline_intervals else "5m"
        )
        init_cfg = load_forecast_cfg(self.db)
        self.forecast = ForecastEnsemble(self.symbols, self.forecast_interval, init_cfg)
        # [ANCHOR:STRAT_ROUTE] begin
        self.streams.feature_engine = self.feature_engine
        self.streams.regime = self.regime
        self.streams.orch = self
        # [ANCHOR:STRAT_ROUTE] end

        rcfg_view = load_risk_cfg(self.db)
        self.risk = RiskEngine(
            self.symbols,
            RiskConfig(
                risk_target_pct=rcfg_view.risk_target_pct,
                corr_cap_per_side=rcfg_view.corr_cap_per_side,
                day_max_loss_pct=rcfg_view.day_max_loss_pct,
                atr_k=rcfg_view.atr_k,
                min_notional=rcfg_view.min_notional,
                equity_override=rcfg_view.equity_override,
            ),
        )

        self.exec_router = OrderRouter(
            self.cli_trade,
            ExecConfig(
                active=exv.active,
                cooldown_s=exv.cooldown_s,
                tol_rel=exv.tol_rel,
                tol_abs=exv.tol_abs,
                order_type=exv.order_type,
                reduce_only=exv.reduce_only,
            ),
        )

        pcv = load_protect_cfg(self.db)
        self.reconciler = Reconciler(
            self.bus, self.db, self.exec_router,
            ProtectConfig(
                slip_warn_pct=pcv.slip_warn_pct,
                slip_max_pct=pcv.slip_max_pct,
                stale_rel=pcv.stale_rel,
                stale_secs=pcv.stale_secs,
                eps_rel=pcv.eps_rel,
                eps_abs=pcv.eps_abs,
                partial_timeout_s=pcv.partial_timeout_s,
                cancel_on_stale=pcv.cancel_on_stale,
            ),
        )

        oov = load_open_orders_cfg(self.db)
        self.oo_mgr = OpenOrdersManager(
            self.cli_trade, self.bus, self.exec_router,
            OOConfig(
                enabled=oov.enabled,
                poll_s=oov.poll_s,
                stale_secs=oov.stale_secs,
                price_drift_pct=oov.price_drift_pct,
                cancel_on_day_cut=oov.cancel_on_day_cut,
                max_open_per_sym=oov.max_open_per_sym,
            ),
        )

        # trade-mode clients for execution-related modules
        self.exec_router.cli = self.cli_trade
        self.oo_mgr.cli = self.cli_trade


        gcv = load_guard_cfg(self.db)
        self.guard = PositionGuard(
            self.bus, self.exec_router,
            GuardConfig(
                enabled=gcv.enabled,
                max_lever_total=gcv.max_lever_total,
                max_lever_per_sym=gcv.max_lever_per_sym,
                stop_pct=gcv.stop_pct,
                trail_activate_pct=gcv.trail_activate_pct,
                trail_width_pct=gcv.trail_width_pct,
            ),
        )

        eqv = load_execq_cfg(self.db)
        self.execq = get_exec_quality(ExecQConfig(
            window_sec=int(eqv.window_sec),
            alert_p90_bps=float(eqv.alert_p90_bps),
            min_fills=int(eqv.min_fills),
            report_sec=int(eqv.report_sec),
        ))

        ol = load_order_ledger_cfg(self.db)
        self.order_ledger = get_order_ledger(
            self.db,
            OLConfig(
                window_sec=int(ol.window_sec),
                report_sec=int(ol.report_sec),
                min_orders=int(ol.min_orders),
            ),
        )
        self.kpi_cfg = load_kpi_cfg(self.db)
        # REPLAY 엔진 준비 (라이브 스트림과 병행하지 않도록 ENV로 제어)
        rcv = load_replay_cfg(self.db)
        self.replay = ReplayEngine(
            self.bus, self.db,
            ReplayConfig(
                enabled=rcv.enabled,
                src=rcv.src,
                speed=rcv.speed,
                loop=rcv.loop,
                default_interval=rcv.default_interval,
            ),
        )
        ohv = load_ops_http_cfg(self.db)
        self.ops_http = OpsHttp(self.bus, OpsHttpConfig(
            enabled=ohv.enabled,
            host=ohv.host,
            port=int(ohv.port),
            ready_max_skew_s=float(ohv.ready_max_skew_s),
        ))

        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._started = False

        # 부팅 요약
        log.info("[BOOT_ENV_SUMMARY] MODE=%s, SYMBOLS=%s, TF_EXEC=%s, TF_SIGNAL=%s", self.mode, self.symbols, self.tf_exec, self.kline_intervals)
        for k in ("DB_PATH", "CONFIG_PATH", "PATCH_LOG"):
            if os.getenv(k):
                log.info("[BOOT_PATH] %s=%s", k, os.getenv(k))

    # -------- optional: simple REST poller (mark price) ----------
    def _price_poller(self, symbol: str, interval_s: float = 3.0) -> None:
        while not self._stop.is_set():
            r = self.cli_data.mark_price(symbol)
            if r.get("ok"):
                d = r["data"]
                price = float(d.get("markPrice", 0.0))
                ts = int(d.get("time") or int(time.time() * 1000))
                self.bus.update_mark(symbol, price, ts)
                log.debug("[PRICE_POLL] %s price=%s ts=%s", symbol, price, ts)
            time.sleep(interval_s)

    # [ANCHOR:ORCH_INTENT_SOURCE]
    def on_bar_close(self, sym: str, itv: str, bus) -> None:
        from ftm2.utils.env import env_str, env_bool
        mode = env_str("STRAT_MODE", "ensemble")
        dummy_enabled = env_bool("DUMMY_INTENT", False)

        intent = None
        if mode == "ensemble":
            rows = self.forecast.process_snapshot(bus.snapshot())
            for r in rows:
                if r.get("symbol") == sym:
                    fc = r.get("forecast", {})
                    intent = {
                        "dir": fc.get("stance", "FLAT"),
                        "score": float(fc.get("score", 0.0)),
                        "reason": "ENSEMBLE",
                        "tf": itv,
                        "ts": int(r.get("T") or 0),
                    }
                    break
        if intent is None and dummy_enabled:
            import random, time as _t
            sc = round(random.uniform(-0.9, 0.9), 1)
            d = "LONG" if sc > 0 else "SHORT" if sc < 0 else "FLAT"
            intent = {"dir": d, "score": float(sc), "reason": "DUMMY", "tf": itv, "ts": int(_t.time() * 1000)}
        if intent is None:
            return
        if intent.get("reason") == "DUMMY" and not dummy_enabled:
            log.debug("[INTENT] skip(dummy)")
            return
        bus.update_intent(sym, intent)
        # 주문 실행은 리스크 루프 → router.sync(snapshot) 경로가 담당합니다.
        # 의도(intent)는 버스에만 반영하고 여기서는 주문을 직접 호출하지 않습니다.
        if not (self.exec_router.cfg.active and getattr(self, "cli_trade", None) and getattr(self.cli_trade, "order_active", False)):
            log.info("[INTENT] %s %s / %.1f / reason=%s", sym, intent["dir"], intent["score"], intent["reason"])
    # [ANCHOR:ORCH_INTENT_SOURCE] end

    def _features_loop(self, period_s: float = 0.5) -> None:
        """
        닫힌 봉을 감지해 피처 계산 후 StateBus에 갱신.
        """
        while not self._stop.is_set():
            snap = self.bus.export_snapshot() if hasattr(self.bus, "export_snapshot") else self.bus.snapshot()
            rows = self.feature_engine.process_snapshot(snap)
            for r in rows:
                self.bus.update_features(r["symbol"], r["interval"], r["features"])
                log.debug("[FEATURE_UPDATE] %s %s T=%s", r["symbol"], r["interval"], r["T"])
            time.sleep(period_s)


    def _regime_loop(self, period_s: float = 0.5) -> None:
        """
        닫힌 봉 기반 피처에서 레짐을 산출하고, 변경 시만 StateBus/알림을 갱신한다.
        """
        while not self._stop.is_set():
            snap = self.bus.export_snapshot() if hasattr(self.bus, "export_snapshot") else self.bus.snapshot()
            changes = self.regime.process_snapshot(snap)
            for chg in changes:
                sym = chg["symbol"]
                itv = chg["interval"]
                reg = chg["regime"]
                self.bus.update_regime(sym, itv, reg)
                msg = (
                    f"🧭 레짐 전환 — {sym}/{itv}: **{reg['label']}** "
                    f"(코드: {reg['code']}, ema={reg['ema_spread']:.5f}, rv_pr={reg['rv_pr']:.3f})"
                )
                log.info(msg)
                try:
                    self.db.record_event("INFO", "regime", msg)
                except Exception:
                    pass
                try:
                    enqueue_alert(msg, intent="logs")
                except Exception:
                    pass
            time.sleep(period_s)


    def _reload_cfg_loop(self, period_s: float = 10.0) -> None:
        """
        DB/ENV에서 예측 파라미터를 주기적으로 재로딩.
        변경이 감지되면 self.forecast.cfg 를 교체한다.
        """
        import dataclasses
        while not self._stop.is_set():
            try:
                new_cfg = load_forecast_cfg(self.db)
                if dataclasses.asdict(new_cfg) != dataclasses.asdict(self.forecast.cfg):
                    self.forecast.cfg = new_cfg
                    log.info("[FORECAST_CFG_RELOAD] 가중치/임계 업데이트 적용: %s", new_cfg)
            except Exception as e:
                log.warning("[FORECAST_CFG_RELOAD] 실패: %s", e)

            try:
                new_risk_view = load_risk_cfg(self.db)
                cur = self.risk.cfg
                if (
                    cur.risk_target_pct != new_risk_view.risk_target_pct
                    or cur.corr_cap_per_side != new_risk_view.corr_cap_per_side
                    or cur.day_max_loss_pct != new_risk_view.day_max_loss_pct
                    or cur.atr_k != new_risk_view.atr_k
                    or cur.min_notional != new_risk_view.min_notional
                    or cur.equity_override != new_risk_view.equity_override
                ):
                    self.risk.cfg = RiskConfig(
                        risk_target_pct=new_risk_view.risk_target_pct,
                        corr_cap_per_side=new_risk_view.corr_cap_per_side,
                        day_max_loss_pct=new_risk_view.day_max_loss_pct,
                        atr_k=new_risk_view.atr_k,
                        min_notional=new_risk_view.min_notional,
                        equity_override=new_risk_view.equity_override,
                    )
                    log.info("[RISK_CFG_RELOAD] 적용: %s", self.risk.cfg)
            except Exception as e:
                log.warning("[RISK_CFG_RELOAD] 실패: %s", e)

            try:
                new_exec = load_exec_cfg(self.db)
                cur = self.exec_router.cfg
                if (
                    cur.active != new_exec.active
                    or cur.cooldown_s != new_exec.cooldown_s
                    or cur.tol_rel != new_exec.tol_rel
                    or cur.tol_abs != new_exec.tol_abs
                    or cur.order_type != new_exec.order_type
                    or cur.reduce_only != new_exec.reduce_only
                ):
                    self.exec_router.cfg = ExecConfig(
                        active=new_exec.active,
                        cooldown_s=new_exec.cooldown_s,
                        tol_rel=new_exec.tol_rel,
                        tol_abs=new_exec.tol_abs,
                        order_type=new_exec.order_type,
                        reduce_only=new_exec.reduce_only,
                    )
                    log.info("[EXEC_CFG_RELOAD] 적용: %s", self.exec_router.cfg)
            except Exception as e:
                log.warning("[EXEC_CFG_RELOAD] 실패: %s", e)

            try:
                new_pcv = load_protect_cfg(self.db)
                rc = self.reconciler.cfg
                if (
                    rc.slip_warn_pct != new_pcv.slip_warn_pct
                    or rc.slip_max_pct != new_pcv.slip_max_pct
                    or rc.stale_rel != new_pcv.stale_rel
                    or rc.stale_secs != new_pcv.stale_secs
                    or rc.eps_rel != new_pcv.eps_rel
                    or rc.eps_abs != new_pcv.eps_abs
                    or rc.partial_timeout_s != new_pcv.partial_timeout_s
                    or rc.cancel_on_stale != new_pcv.cancel_on_stale

                ):
                    self.reconciler.cfg = ProtectConfig(
                        slip_warn_pct=new_pcv.slip_warn_pct,
                        slip_max_pct=new_pcv.slip_max_pct,
                        stale_rel=new_pcv.stale_rel,
                        stale_secs=new_pcv.stale_secs,
                        eps_rel=new_pcv.eps_rel,
                        eps_abs=new_pcv.eps_abs,
                        partial_timeout_s=new_pcv.partial_timeout_s,
                        cancel_on_stale=new_pcv.cancel_on_stale,

                    )
                    log.info("[PROTECT_CFG_RELOAD] 적용: %s", self.reconciler.cfg)
            except Exception as e:
                log.warning("[PROTECT_CFG_RELOAD] 실패: %s", e)

            try:
                new_oov = load_open_orders_cfg(self.db)
                cur = self.oo_mgr.cfg
                if (cur.enabled != new_oov.enabled or
                    cur.poll_s != new_oov.poll_s or
                    cur.stale_secs != new_oov.stale_secs or
                    cur.price_drift_pct != new_oov.price_drift_pct or
                    cur.cancel_on_day_cut != new_oov.cancel_on_day_cut or
                    cur.max_open_per_sym != new_oov.max_open_per_sym):
                    self.oo_mgr.cfg = OOConfig(
                        enabled=new_oov.enabled,
                        poll_s=new_oov.poll_s,
                        stale_secs=new_oov.stale_secs,
                        price_drift_pct=new_oov.price_drift_pct,
                        cancel_on_day_cut=new_oov.cancel_on_day_cut,
                        max_open_per_sym=new_oov.max_open_per_sym,
                    )
                    log.info('[OO_CFG_RELOAD] 적용: %s', self.oo_mgr.cfg)
            except Exception as e:
                log.warning('[OO_CFG_RELOAD] 실패: %s', e)
            try:
                new_eqv = load_execq_cfg(self.db)
                cur = self.execq.cfg
                if (
                    cur.window_sec != new_eqv.window_sec or
                    cur.alert_p90_bps != new_eqv.alert_p90_bps or
                    cur.min_fills != new_eqv.min_fills or
                    cur.report_sec != new_eqv.report_sec
                ):
                    self.execq.cfg = ExecQConfig(
                        window_sec=new_eqv.window_sec,
                        alert_p90_bps=new_eqv.alert_p90_bps,
                        min_fills=new_eqv.min_fills,
                        report_sec=new_eqv.report_sec,
                    )
                    log.info('[EQ_CFG_RELOAD] 적용: %s', self.execq.cfg)
            except Exception as e:
                log.warning('[EQ_CFG_RELOAD] 실패: %s', e)
            try:

                new_gcv = load_guard_cfg(self.db)
                cur = self.guard.cfg
                if (
                    cur.enabled != new_gcv.enabled or
                    cur.max_lever_total != new_gcv.max_lever_total or
                    cur.max_lever_per_sym != new_gcv.max_lever_per_sym or
                    cur.stop_pct != new_gcv.stop_pct or
                    cur.trail_activate_pct != new_gcv.trail_activate_pct or
                    cur.trail_width_pct != new_gcv.trail_width_pct
                ):
                    self.guard.cfg = GuardConfig(
                        enabled=new_gcv.enabled,
                        max_lever_total=new_gcv.max_lever_total,
                        max_lever_per_sym=new_gcv.max_lever_per_sym,
                        stop_pct=new_gcv.stop_pct,
                        trail_activate_pct=new_gcv.trail_activate_pct,
                        trail_width_pct=new_gcv.trail_width_pct,
                    )
                    log.info("[GUARD][CFG] 업데이트 적용: %s", self.guard.cfg)
            except Exception as e:
                log.warning("[GUARD][CFG] reload fail: %s", e)

            try:
                new_k = load_kpi_cfg(self.db)
                cur = self.kpi_cfg
                if (
                    cur.enabled != new_k.enabled
                    or cur.report_sec != new_k.report_sec
                    or cur.to_discord != new_k.to_discord
                    or cur.only_on_change != new_k.only_on_change
                ):
                    self.kpi_cfg = new_k
                    log.info("[KPI] cfg reload: %s", self.kpi_cfg)
            except Exception as e:
                log.warning("[KPI] cfg reload err: %s", e)

            time.sleep(period_s)


    def _forecast_loop(self, period_s: float = 0.5) -> None:
        """
        닫힌 봉 시점에 앙상블 예측을 계산하고 StateBus/DB/알림을 갱신.
        """
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            rows = self.forecast.process_snapshot(snap)
            for r in rows:
                fc = r["forecast"]
                sym = r["symbol"]
                itv = r["interval"]
                self.bus.update_forecast(sym, itv, fc)
                try:
                    msg = (
                        f"🎯 예측 — {sym}/{itv}: score={fc['score']:.3f} "
                        f"p_up={fc['prob_up']:.3f} stance={fc['stance']} (regime={fc['regime']})"
                    )
                    self.db.record_event("INFO", "forecast", msg)
                except Exception:
                    pass
                try:
                    if abs(fc["score"]) >= self.forecast.cfg.strong_thr:
                        arrow = "⬆️" if fc["score"] > 0 else "⬇️"
                        enqueue_alert(
                            f"{arrow} **강신호** — {sym}/{itv} score={fc['score']:.3f} p_up={fc['prob_up']:.3f} regime={fc['regime']}"
                        )
                except Exception:
                    pass
            time.sleep(period_s)


    def _risk_loop(self, period_s: float = 0.5) -> None:
        """
        예측/피처/마크를 바탕으로 목표 포지션을 산출하고 버스/DB/알림을 갱신.
        """
        day_cut_sent = None
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            targets = self.risk.process_snapshot(snap)

            eq = resolve_equity(self.bus)
            long_used = sum(t["target_notional"] for t in targets if t["target_qty"] > 0.0)
            short_used = sum(t["target_notional"] for t in targets if t["target_qty"] < 0.0)

            mapping = {t["symbol"]: t for t in targets}
            self.bus.set_targets(mapping)
            self.bus.set_risk_state({
                "equity": eq,
                "day_pnl_pct": self.risk._day_pnl_pct(snap),
                "day_cut": self.risk.day_cut_on,
                "used_long_ratio": (long_used / eq) if eq > 0 else 0.0,
                "used_short_ratio": (short_used / eq) if eq > 0 else 0.0,
                "corr_cap_per_side": self.risk.cfg.corr_cap_per_side,
            })

            for t in targets:
                log.info(
                    "[RISK] %s side=%s qty=%.6f notional=%.2f reason=%s",
                    t["symbol"],
                    t["side"],
                    t["target_qty"],
                    t["target_notional"],
                    t["reason"],
                )
                try:
                    self.db.record_event(
                        "INFO",
                        "risk",
                        f"{t['symbol']} {t['side']} qty={t['target_qty']:.6f} notional={t['target_notional']:.2f} {t['reason']}",
                    )
                except Exception:
                    pass

            if self.risk.day_cut_on and day_cut_sent is not True:
                msg = (
                    f"🛑 데일리컷 발동: 당일 손실률 ≤ -{self.risk.cfg.day_max_loss_pct:.2f}% — 모든 타깃 0으로 설정"
                )
                log.warning("[DAY_CUT] on")
                try:
                    enqueue_alert(msg, intent="logs")
                    self.db.record_event("WARN", "risk", "DAY_CUT_ON")
                except Exception:
                    pass
                day_cut_sent = True
            elif (not self.risk.day_cut_on) and day_cut_sent is not False:
                log.info("[DAY_CUT] off")
                day_cut_sent = False

            time.sleep(period_s)


    def _exec_loop(self, period_s: float = 1.0) -> None:
        """
        RiskEngine이 계산한 targets를 소비하여 주문(또는 드라이런)을 수행.
        """
        while not self._stop.is_set():
            snap = self.bus.export_snapshot() if hasattr(self.bus, "export_snapshot") else self.bus.snapshot()
            # [ANCHOR:STRAT_ROUTE] begin
            if not is_exec_enabled(self.bus):
                log.info("[EXEC] disabled (source=PANEL|ENV)")
                time.sleep(period_s)
                continue
            # [ANCHOR:STRAT_ROUTE] end
            try:
                intents = snap.get("intents") or {}
                n_int = (
                    sum(len(v) for v in intents.values())
                    if isinstance(intents, dict)
                    else (len(intents) if intents else 0)
                )
                log.info(f"[EXEC.SYNC] intents={n_int} (tick)")
                res = self.exec_router.sync(snap)
                for r in res:
                    msg = (
                        f"{r['mode']} {r['symbol']} {r['side']} Δ={r['delta_qty']:.6f} "
                        f"qty={r['qty_sent']:.6f} {r['reason']}"
                    )
                    log.info("[EXEC] %s", msg)
                    try:
                        self.db.record_event("INFO", "exec", msg)
                    except Exception:
                        pass

                    # Order submit → Ledger
                    try:
                        self.order_ledger.on_submit({
                            "ts_submit": int(self.bus.snapshot().get("now_ts") or 0),
                            "symbol": r["symbol"],
                            "side": r.get("side"),
                            "type": self.exec_router.cfg.order_type,
                            "price": float((self.bus.snapshot().get("marks") or {}).get(r["symbol"], {}).get("price") or 0.0),
                            "orig_qty": float(r.get("qty_sent") or 0.0),
                            "mode": "LIVE" if self.exec_router.cfg.active else "DRY",
                            "reduce_only": bool("reduceOnly" in {}),
                            "client_order_id": None,
                            "order_id": str((r.get("result") or {}).get("orderId") or ""),
                        })
                    except Exception:
                        pass
            except Exception as e:
                log.warning(f"[EXEC.SYNC][WARN] {e}")
            time.sleep(period_s)

    # [ANCHOR:ORCH_EXEC_TOGGLE]
    async def on_exec_toggle(self, active: bool) -> None:
        # 실행 라우터 on/off
        self.exec_router.cfg.active = bool(active)
        if getattr(self, "cli_trade", None):
            self.cli_trade.order_active = bool(active)
        log.info("[EXEC] toggle active=%s source=PANEL", active)


    def _reconcile_loop(self, period_s: float = 0.5) -> None:
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            try:
                res = self.reconciler.process(snap)
                if res.get("fills_saved"):
                    log.info(
                        "[RECON] fills_saved=%s slip_warns=%d nudges=%d",
                        res["fills_saved"],
                        len(res.get("slip_warns", [])),
                        len(res.get("nudges", [])),
                    )
            except Exception as e:
                log.warning("[RECON] loop err: %s", e)
            time.sleep(period_s)



    def _oo_loop(self) -> None:
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            try:
                res = self.oo_mgr.poll_once(snap)
                if res.get('cancelled'):
                    for c in res['cancelled']:
                        msg = '🧹 오더 취소 — {symbol} oid={orderId} ({reason})'.format(**c)
                        log.info('[OO] %s', msg)
                        try:
                            self.db.record_event('INFO', 'open_orders', msg)
                        except Exception:
                            pass
            except Exception as e:
                log.warning('[OO] loop err: %s', e)
            time.sleep(max(0.5, float(self.oo_mgr.cfg.poll_s)))


    def _guard_loop(self, period_s: float = 0.5) -> None:
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            try:
                acts = self.guard.process(snap)
                if acts:
                    try:
                        self.bus.set_guard_state({"last_actions": acts[-5:]})
                    except Exception:
                        pass
                    for a in acts:
                        msg = f"{a['action']} {a['symbol']} qty={a['qty']:.6f} reason={a['reason']}"
                        self.db.record_event("WARN", "guard", msg)
            except Exception as e:
                log.warning("[GUARD] loop err: %s", e)
            time.sleep(period_s)


    def _execq_loop(self) -> None:
        """
        롤링 윈도우 실행 품질을 주기 보고하고 임계 초과 시 알림.
        """
        from ftm2.discord_bot.notify import enqueue_alert as _alert
        while not self._stop.is_set():
            try:
                s = self.execq.summary()
                try:
                    self.bus.set_guard_state({**(self.bus.snapshot().get("guard") or {}), "exec_quality": s})
                except Exception:
                    pass
                if s.get("samples", 0) >= self.execq.cfg.min_fills:
                    p90 = float((s.get("slip_bps_overall") or {}).get("p90") or 0.0)
                    msg = (
                        f"📊 실행 품질 — 샘플 {s['samples']}개 / bps(avg={s['slip_bps_overall']['avg']:.2f}, "
                        f"p50={s['slip_bps_overall']['p50']:.2f}, p90={p90:.2f}) / 넛지 {s['nudges']} / 취소 {s['cancels']}"
                    )
                    try:
                        self.db.record_event("INFO", "exec_quality", msg)
                    except Exception:
                        pass
                    if p90 >= self.execq.cfg.alert_p90_bps:
                        try:
                            _alert(
                                f"🚨 실행 슬리피지 경보 — p90={p90:.1f}bps (임계 {self.execq.cfg.alert_p90_bps:.1f}bps 초과)",
                                intent="logs",
                            )
                        except Exception:
                            pass
            except Exception as e:
                log.warning("[EQ] loop err: %s", e)
            time.sleep(max(2.0, float(self.execq.cfg.report_sec)))

    def _order_ledger_loop(self) -> None:
        while not self._stop.is_set():
            try:
                s = self.order_ledger.summary()
                try:
                    g = self.bus.snapshot().get("guard") or {}
                    g2 = {**g, "exec_ledger": s}
                    self.bus.set_guard_state(g2)
                except Exception:
                    pass
                if s.get("orders", 0) >= self.order_ledger.cfg.min_orders:
                    msg = (
                        f"🧾 주문원장 — {s['orders']}건 / "
                        f"체결률={s['fill_rate']*100:.1f}% 취소율={s['cancel_rate']*100:.1f}% "
                        f"TTF(avg={s['avg_ttf_ms']:.0f}ms,p50={s['p50_ttf_ms']:.0f}ms)"
                    )
                    try:
                        self.db.record_event("INFO", "order_ledger", msg)
                    except Exception:
                        pass
            except Exception as e:
                log.warning("[LEDGER] loop err: %s", e)
            time.sleep(max(5.0, float(self.order_ledger.cfg.report_sec)))


    def _kpi_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.kpi_cfg.enabled:
                    time.sleep(2.0)
                    continue
                compute_kpi_snapshot(self.bus)
                txt = render_kpi_message(self.bus)
                log.info("[KPI] %s", txt.replace("\n", " | "))
                if self.kpi_cfg.to_discord:
                    try:
                        enqueue_alert(txt, intent="panel")
                    except Exception:
                        pass
            except Exception as e:
                log.warning("[KPI] loop err: %s", e)
            time.sleep(max(3.0, float(self.kpi_cfg.report_sec)))

    # [ANCHOR:ORCH_EQUITY_LOOP] begin
    def _equity_loop(self, period_s: int | None = None) -> None:
        period = _clamped_interval("EQUITY_POLL_SEC", period_s or 20, 5)
        backoff = period
        while not self._stop.is_set():
            try:
                snap = self.cli_trade.account_snapshot()  # ← 단일 진실원
                if snap:
                    eq = float(snap.get("equity", 0.0))
                    wall = float(snap.get("wallet", 0.0))
                    avail = float(snap.get("avail", 0.0))

                    if not hasattr(self.bus, "state"):
                        class _S:  # type: ignore
                            pass

                        self.bus.state = _S()
                    self.bus.state.equity_usdt = eq

                    self.bus.set_account(
                        {
                            "ccy": "USDT",
                            "totalWalletBalance": wall,
                            "availableBalance": avail,
                            "totalUnrealizedProfit": float(snap.get("upnl", 0.0)),
                            "totalMarginBalance": eq,
                            # 호환 키
                            "wallet": wall,
                            "avail": avail,
                            "upnl": float(snap.get("upnl", 0.0)),
                            "equity": eq,
                        }
                    )
                    log.info(
                        "[EQUITY] updated: totalWallet=%.2f available=%.2f equity=%.2f src=ACCOUNT",
                        wall,
                        avail,
                        eq,
                    )
                    equity_heartbeat_loop(self.bus)
                    backoff = period
            except Exception as e:
                log.warning(
                    "E_BAL_POLL_FAIL code=%s msg=%s backoff=%ss",
                    getattr(e, "code", ""),
                    getattr(e, "msg", str(e)),
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, period * 5)
                continue
            _sleep_with_jitter(period)
    # [ANCHOR:ORCH_EQUITY_LOOP] end

    def _positions_loop(self, period_s: int | None = None) -> None:
        period = period_s or env_int("POSITIONS_POLL_SEC", 10)
        backoff = period
        while not self._stop.is_set():
            try:

                pos = self.cli_trade.fetch_positions(self.symbols)
                if pos:
                    self.bus.set_positions(pos)
            except Exception as e:
                log.warning("E_POS_POLL_FAIL %r backoff=%s", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, period * 5)
                continue
            backoff = period
            time.sleep(period)


    def _positions_loop(self, period_s: int | None = None) -> None:
        period = period_s or env_int("POSITIONS_POLL_SEC", 10)
        backoff = period
        while not self._stop.is_set():
            try:
                pos = self.cli_trade.fetch_positions(self.symbols)
                if pos:
                    self.bus.set_positions(pos)
            except Exception as e:
                log.warning("E_POS_POLL_FAIL %r backoff=%s", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, period * 5)
                continue
            backoff = period
            time.sleep(period)


    def _warmup(self, n: int = 800) -> None:
        for s in self.symbols:
            for tf in self.kline_intervals:
                try:

                    self.cli_trade.ensure_http()
                except Exception as e:
                    log.warning(
                        "WARMUP_FAIL %s %s HTTP driver not available (%s)", s, tf, e
                    )
                    return
                try:

                    rows = get_klines(s, tf, limit=n)
                except Exception as e:
                    log.warning("WARMUP_FAIL %s %s %s", s, tf, e)
                    continue
                for r in rows:
                    try:
                        bar = {
                            "t": int(r[0]),
                            "T": int(r[6]),
                            "o": float(r[1]),
                            "h": float(r[2]),
                            "l": float(r[3]),
                            "c": float(r[4]),
                            "v": float(r[5]),
                            "x": True,
                        }
                    except Exception:
                        continue
                    self.bus.update_kline(s, tf, bar)
                    self.feature_engine.update(s, tf, self.bus)
                    self.regime.update(s, tf, self.bus)
                log.info("[WARMUP] %s/%s bars=%d", s, tf, len(rows))


    def start(self) -> None:
        if self._started:
            log.info("[APP] start() skipped (already started)")
            return
        self._started = True
        # 심볼별 마크프라이스 폴러는 M1.1 임시 → WS로 대체
        # for sym in self.symbols:
        #     t = threading.Thread(target=self._price_poller, args=(sym,), name=f"poll:{sym}", daemon=True)
        #     t.start()
        #     self._threads.append(t)

        # HIST warmup
        self._warmup()

        # WS 스트림 또는 리플레이 시작
        if getattr(self.replay.cfg, "enabled", False):
            log.info("[APP] REPLAY 모드: 파일=%s 속도=%.2fx", self.replay.cfg.src, self.replay.cfg.speed)
            try:
                self.replay.start()
            except Exception as e:
                log.warning("[APP][REPLAY] 시작 실패: %s", e)
        else:
            self.streams.start()

        # KPI 초기화 전에 계좌/포지션을 한 번 갱신해 초기 값이 남지 않도록
        try:
            snap = self.cli_trade.account_snapshot()
            if snap:
                self.bus.set_account({
                    "ccy": "USDT",
                    "totalWalletBalance": snap.get("wallet", 0.0),
                    "availableBalance": snap.get("avail", 0.0),
                    "totalUnrealizedProfit": snap.get("upnl", 0.0),
                    "totalMarginBalance": snap.get("equity", 0.0),
                    "wallet": snap.get("wallet", 0.0),
                    "avail": snap.get("avail", 0.0),
                    "upnl": snap.get("upnl", 0.0),
                    "equity": snap.get("equity", 0.0),
                })
                log.info("[EQUITY] bootstrap: totalMarginBalance=%.2f", snap.get("equity", 0.0))

                try:
                    compute_kpi_snapshot(self.bus)
                except Exception:
                    pass
        except Exception as e:
            log.warning("E_EQUITY_BOOTSTRAP %s", e)
        try:
            pos = self.cli_trade.fetch_positions(self.symbols)
            if pos:
                self.bus.set_positions(pos)
        except Exception:
            pass

        # 피처 루프 시작
        t = threading.Thread(target=self._features_loop, name="features", daemon=True)
        t.start()
        self._threads.append(t)

        # 레짐 루프 시작
        t = threading.Thread(target=self._regime_loop, name="regime", daemon=True)
        t.start()
        self._threads.append(t)

        # 예측 루프 시작
        t = threading.Thread(target=self._forecast_loop, name="forecast", daemon=True)
        t.start()
        self._threads.append(t)

        # 리스크 루프 시작
        t = threading.Thread(target=self._risk_loop, name="risk", daemon=True)
        t.start()
        self._threads.append(t)

        # 실행 루프 시작
        t = threading.Thread(target=self._exec_loop, name="exec", daemon=True)
        t.start()
        self._threads.append(t)

        # 리컨실 루프 시작
        t = threading.Thread(target=self._reconcile_loop, name="reconcile", daemon=True)
        t.start()
        self._threads.append(t)
        
        # 오픈오더 루프 시작
        t = threading.Thread(target=self._oo_loop, name="open-orders", daemon=True)
        t.start()
        self._threads.append(t)

        # 가드 루프 시작
        t = threading.Thread(target=self._guard_loop, name="guard", daemon=True)
        t.start()
        self._threads.append(t)

        # 실행 품질 루프 시작
        t = threading.Thread(target=self._execq_loop, name="exec-quality", daemon=True)
        t.start()
        self._threads.append(t)

        # 주문 원장 리포트 루프 시작
        t = threading.Thread(target=self._order_ledger_loop, name="order-ledger", daemon=True)
        t.start()
        self._threads.append(t)

        # KPI 루프 시작
        t = threading.Thread(target=self._kpi_loop, name="kpi", daemon=True)
        t.start()
        self._threads.append(t)

        # Equity 폴링 루프 시작(중복 방지)
        with _equity_lock:
            global _EQUITY_LOOP_STARTED
            if not _EQUITY_LOOP_STARTED:
                te = threading.Thread(target=self._equity_loop, name="equity-poll", daemon=True)
                te.start()
                self._threads.append(te)
                _EQUITY_LOOP_STARTED = True

        # Positions 폴링 (중복 방지)
        if not getattr(self, "_pos_thread_started", False):
            tp = threading.Thread(target=self._positions_loop, name="pos-poll", daemon=True)
            tp.start()
            self._threads.append(tp)
            self._pos_thread_started = True


        # 설정 핫리로드
        t = threading.Thread(target=self._reload_cfg_loop, name="cfg-reload", daemon=True)
        t.start()
        self._threads.append(t)

        # 더미 전략 루프
        if env_bool("DUMMY_INTENT", False):
            st = threading.Thread(target=self._strategy_loop, name="strategy", daemon=True)
            st.start()
            self._threads.append(st)
        else:
            log.info("[STRATEGY] dummy-intent disabled (DUMMY_INTENT=0)")

        # 하트비트 스레드(중복 방지)
        with _heartbeat_lock:
            global _HEARTBEAT_STARTED
            if not _HEARTBEAT_STARTED:
                t = threading.Thread(target=self._heartbeat, name="heartbeat", daemon=True)
                t.start()
                self._threads.append(t)
                _HEARTBEAT_STARTED = True



        # Discord 봇 (토큰 없으면 내부에서 자동 비활성 로그 후 종료)
        if (os.getenv("DISCORD_ENABLED", "true").lower() in ("1", "true", "yes")):
            dt = threading.Thread(target=run_discord_bot, args=(self.bus,), name="discord-bot", daemon=True)
            dt.start()
            self._threads.append(dt)

        try:
            self.ops_http.start()
        except Exception as e:
            log.warning("[OPS_HTTP] start err: %s", e)

        # 시그널 핸들
        try:
            signal.signal(signal.SIGINT, self._signal_stop)
            signal.signal(signal.SIGTERM, self._signal_stop)
        except Exception:
            pass

    def _heartbeat(self, period_s: int = 10) -> None:
        while not self._stop.is_set():
            # 계정/포지션 주기 갱신
            try:
                snap = self.cli_trade.account_snapshot()
                if snap:
                    self.bus.set_account({
                        "ccy": "USDT",
                        "totalWalletBalance": snap.get("wallet", 0.0),
                        "availableBalance": snap.get("avail", 0.0),
                        "totalUnrealizedProfit": snap.get("upnl", 0.0),
                        "totalMarginBalance": snap.get("equity", 0.0),
                        "wallet": snap.get("wallet", 0.0),
                        "avail": snap.get("avail", 0.0),
                        "upnl": snap.get("upnl", 0.0),
                        "equity": snap.get("equity", 0.0),
                    })

                    try:
                        if not hasattr(self.bus, "state"):
                            class _S:  # pragma: no cover - simple holder
                                pass
                            self.bus.state = _S()
                        self.bus.state.equity_usdt = float(snap.get("equity") or 0.0)
                    except Exception:
                        pass
                    equity_heartbeat_loop(self.bus)
                    compute_kpi_snapshot(self.bus)
                    log.info("[EQUITY] updated: totalMarginBalance=%.2f src=ACCOUNT", snap.get("equity", 0.0))

            except Exception as e:
                log.warning("E_EQUITY_POLL_FAIL %s", e)
            try:
                pos = self.cli_trade.fetch_positions(self.symbols)
                if pos:
                    self.bus.set_positions(pos)
            except Exception:
                pass

            snap = self.bus.snapshot()
            marks = snap["marks"]
            lines = []
            for s in self.symbols:
                if s in marks:
                    lines.append(f"{s}={marks[s]['price']}")
            marks_str = ", ".join(lines)
            uptime_s = self.bus.uptime_s()
            exec_on = bool(
                getattr(self.exec_router.cfg, "active", False)
                or (
                    getattr(self, "bus", None)
                    and isinstance(self.bus.config, dict)
                    and self.bus.config.get("exec_active")
                )
            )
            log.info(
                "[HEARTBEAT] mode=%s exec=%s uptime=%ss symbols=%d marks={%s}",
                self.mode,
                ("on" if exec_on else "off"),
                uptime_s,
                len(self.symbols),
                marks_str,
            )
            time.sleep(period_s)

    def _strategy_loop(self, period_s: float = 1.0) -> None:
        """
        닫힌 봉을 감지해 더미 의도 신호를 방출(드라이런).
        - 콘솔 로그, DB events, Discord 알림(가능 시)
        """
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            intents = self.forecaster.evaluate(snap)
            for it in intents:
                sym = it["symbol"]
                side = it["side"]
                sc = float(it["score"])
                bp = abs(sc) * 10000.0
                msg = f"📡 {sym} 의도만: **{side}** / +{bp:.1f} / 사유: DUMMY"
                log.info("[INTENT] %s", msg)
                try:
                    self.db.record_event("INFO", "intent", msg)
                except Exception:
                    pass
                try:
                    enqueue_alert(msg, intent="signals")
                except Exception:
                    pass
            time.sleep(period_s)


    def _signal_stop(self, *_):
        log.info("[SHUTDOWN] stop requested")
        self.stop()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self.streams.stop_all()
        except Exception:
            pass
        try:
            self.replay.stop()
        except Exception:
            pass
        try:
            self.ops_http.stop()
        except Exception:
            pass
        for t in list(self._threads):
            if t.is_alive():
                t.join(timeout=2.0)
        try:
            self.db.record_event("INFO", "system", "shutdown")
            self.db.close()
        except Exception:
            pass

        log.info("[SHUTDOWN] orchestrator stopped")


def run() -> None:
    orch = Orchestrator()
    orch.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        orch.stop()


if __name__ == "__main__":
    run()
