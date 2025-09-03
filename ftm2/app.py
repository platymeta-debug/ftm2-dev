# -*- coding: utf-8 -*-
"""
FTM2 Orchestrator (minimal)
- ENV 로딩 → BinanceClient 생성 → WS 스트림(StreamManager) 시작
- 10초 하트비트 로그
- Ctrl+C 안전 종료
"""
from __future__ import annotations

import os
import time
import signal
import logging
import threading
from typing import List

# 로컬 모듈
try:
    from ftm2.core.env import load_env_chain
    from ftm2.core.state import StateBus
    from ftm2.data.streams import StreamManager
except Exception:  # pragma: no cover
    from core.env import load_env_chain  # type: ignore
    from core.state import StateBus  # type: ignore
    from data.streams import StreamManager  # type: ignore

try:
    from ftm2.exchange.binance import BinanceClient
except Exception:  # pragma: no cover
    from exchange.binance import BinanceClient  # type: ignore

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
    from ftm2.discord_bot.notify import enqueue_alert
except Exception:  # pragma: no cover
    from discord_bot.notify import enqueue_alert  # type: ignore

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



log = logging.getLogger("ftm2.orch")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# [ANCHOR:ORCH]
class Orchestrator:
    def __init__(self) -> None:
        self.env = load_env_chain()
        self.mode = (os.getenv("MODE") or "testnet").lower()
        self.symbols: List[str] = [s.strip() for s in (os.getenv("SYMBOLS") or "BTCUSDT,ETHUSDT").split(",") if s.strip()]
        self.tf_exec = os.getenv("TF_EXEC") or "1m"
        self.kline_intervals = [s.strip() for s in (os.getenv("TF_SIGNAL") or "5m,15m,1h,4h").split(",") if s.strip()]
        self.eval_interval = self.kline_intervals[0] if self.kline_intervals else "5m"
        self.regime_interval = self.kline_intervals[0] if self.kline_intervals else "5m"
        self.bus = StateBus()
        self.cli = BinanceClient.from_env(order_active=False)
        self.streams = StreamManager(self.cli, self.bus, self.symbols, self.kline_intervals, use_mark=True, use_user=True)
        self.db_path = os.getenv("DB_PATH") or "./runtime/trader.db"
        self.db = Persistence(self.db_path)
        self.db.ensure_schema()
        try:
            self.db.record_event("INFO", "system", "boot")
        except Exception:
            pass

        self.forecaster = DummyForecaster(self.symbols, self.eval_interval)
        self.feature_engine = FeatureEngine(self.symbols, self.kline_intervals, FeatureConfig())
        self.regime = RegimeClassifier(self.symbols, self.regime_interval, RegimeConfig())
        self.forecast_interval = getattr(self, "regime_interval", None) or (
            self.kline_intervals[0] if self.kline_intervals else "5m"
        )
        init_cfg = load_forecast_cfg(self.db)
        self.forecast = ForecastEnsemble(self.symbols, self.forecast_interval, init_cfg)

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

        exv = load_exec_cfg(self.db)
        self.exec_router = OrderRouter(
            self.cli,
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
            ),
        )


        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

        # 부팅 요약
        log.info("[BOOT_ENV_SUMMARY] MODE=%s, SYMBOLS=%s, TF_EXEC=%s, TF_SIGNAL=%s", self.mode, self.symbols, self.tf_exec, self.kline_intervals)
        for k in ("DB_PATH", "CONFIG_PATH", "PATCH_LOG"):
            if os.getenv(k):
                log.info("[BOOT_PATH] %s=%s", k, os.getenv(k))

    # -------- optional: simple REST poller (mark price) ----------
    def _price_poller(self, symbol: str, interval_s: float = 3.0) -> None:
        while not self._stop.is_set():
            r = self.cli.mark_price(symbol)
            if r.get("ok"):
                d = r["data"]
                price = float(d.get("markPrice", 0.0))
                ts = int(d.get("time") or int(time.time() * 1000))
                self.bus.update_mark(symbol, price, ts)
                log.debug("[PRICE_POLL] %s price=%s ts=%s", symbol, price, ts)
            time.sleep(interval_s)

    def _features_loop(self, period_s: float = 0.5) -> None:
        """
        닫힌 봉을 감지해 피처 계산 후 StateBus에 갱신.
        """
        while not self._stop.is_set():
            snap = self.bus.snapshot()
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
            snap = self.bus.snapshot()
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
                ):
                    self.reconciler.cfg = ProtectConfig(
                        slip_warn_pct=new_pcv.slip_warn_pct,
                        slip_max_pct=new_pcv.slip_max_pct,
                        stale_rel=new_pcv.stale_rel,
                        stale_secs=new_pcv.stale_secs,
                    )
                    log.info("[PROTECT_CFG_RELOAD] 적용: %s", self.reconciler.cfg)
            except Exception as e:
                log.warning("[PROTECT_CFG_RELOAD] 실패: %s", e)
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

            eq = 0.0
            try:
                eq = float(self.risk._equity(snap))
            except Exception:
                pass
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
            snap = self.bus.snapshot()
            try:
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
            except Exception as e:
                log.warning("[EXEC_ERR] %s", e)
            time.sleep(period_s)


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


    def start(self) -> None:
        # 심볼별 마크프라이스 폴러는 M1.1 임시 → WS로 대체
        # for sym in self.symbols:
        #     t = threading.Thread(target=self._price_poller, args=(sym,), name=f"poll:{sym}", daemon=True)
        #     t.start()
        #     self._threads.append(t)

        # WS 스트림 시작
        self.streams.start()

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

        # 설정 핫리로드
        t = threading.Thread(target=self._reload_cfg_loop, name="cfg-reload", daemon=True)
        t.start()
        self._threads.append(t)

        # 더미 전략 루프
        st = threading.Thread(target=self._strategy_loop, name="strategy", daemon=True)
        st.start()
        self._threads.append(st)


        # 하트비트 스레드
        t = threading.Thread(target=self._heartbeat, name="heartbeat", daemon=True)
        t.start()
        self._threads.append(t)


        # Discord 봇 (토큰 없으면 내부에서 자동 비활성 로그 후 종료)
        dt = threading.Thread(target=run_discord_bot, args=(self.bus,), name="discord-bot", daemon=True)
        dt.start()
        self._threads.append(dt)


        # 시그널 핸들
        try:
            signal.signal(signal.SIGINT, self._signal_stop)
            signal.signal(signal.SIGTERM, self._signal_stop)
        except Exception:
            pass

    def _heartbeat(self, period_s: int = 10) -> None:
        while not self._stop.is_set():
            snap = self.bus.snapshot()
            marks = snap["marks"]
            lines = []
            for s in self.symbols:
                if s in marks:
                    lines.append(f"{s}={marks[s]['price']}")
            uptime = self.bus.uptime_s()
            log.info("[HEARTBEAT] mode=%s uptime=%ss symbols=%d marks={%s}", self.mode, uptime, len(marks), ", ".join(lines))
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
            self.streams.stop()
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
