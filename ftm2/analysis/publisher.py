# -*- coding: utf-8 -*-
"""Discord 실시간 분석 리포트 발행"""
# [ANCHOR:ANALYSIS_PUB]

import os, time, asyncio
import discord, logging
from ftm2.utils.env import env_str, env_int
from ftm2.db import init_db
from ftm2.dashboard import _cfg_get, _cfg_set

class AnalysisPublisher:
    def __init__(self, bot, bus, interval_s: int | None = None):
        self.bot = bot
        self.bus = bus
        self.intv = interval_s or env_int("ANALYSIS_REPORT_SEC", 60)
        self.log = logging.getLogger("ftm2.analysis")
        self._msg = None
        self._task = None
        db_path = os.getenv("DB_PATH", "./runtime/trader.db")
        self.db = init_db(db_path)


    async def _ensure_channel(self):
        ids = [
            env_str("DISCORD_CHANNEL_ID_ANALYSIS", ""),
            env_str("DISCORD_CHANNEL_ID_DASHBOARD", ""),
            env_str("DISCORD_CHANNEL_ID_PANEL", ""),
        ]
        ch_id = next((int(x) for x in ids if x and x.isdigit()), 0)
        if not ch_id:
            raise RuntimeError("No analysis-capable channel configured")
        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
        return ch


    async def _ensure_message(self):
        ch = await self._ensure_channel()
        mid = _cfg_get(self.db, "ANALYSIS_MSG_ID")
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(int(mid))
            except Exception:
                msg = None
        if not msg:
            msg = await ch.send("📈 분석 초기화 중…")
            _cfg_set(self.db, "ANALYSIS_MSG_ID", str(msg.id))
        self._msg = msg
        return msg

    # [ANCHOR:ANALYSIS_PUBLISHER]
    def _render(self, snap: dict) -> str:
        """
        분석 리포트 v2: ScoreDetail/게이트/레디니스/플랜 및 TF 합의 출력.
        기존 snapshot(marks/features/regimes/forecasts)을 사용하여
        간이 state 어댑터를 구성해 scoring/ticket 모듈을 재사용합니다.
        """
        import json, os, math
        from ftm2.analysis.scoring import compute_multi_tf
        from ftm2.analysis.ticket import synthesize_ticket

        # --- 간이 state 어댑터 구성 ---
        class _S:
            pass
        state = _S()
        # marks: {sym: price}로 평탄화
        marks_raw = snap.get("marks") or {}
        state.marks = {k: float(v.get("price") or 0.0) for k, v in marks_raw.items()}
        # regime: {sym: {tf: regime}}
        regimes = snap.get("regimes") or {}
        state.regime = {}
        for (sym, tf), rg in regimes.items():
            state.regime.setdefault(sym, {})[tf] = rg
        # features 접근자 (scoring이 요구)
        feats_map = snap.get("features") or {}
        def _compute_features(sym, tf):
            f = feats_map.get((sym, tf), {}) or {}
            # scoring이 기대하는 키셋: ema, rv20, atr, ret1, rv_pr, asof
            return {
                "ema":  f.get("ema"),
                "rv20": f.get("rv20"),
                "atr":  f.get("atr"),
                "ret1": f.get("ret1"),
                "rv_pr":f.get("rv_pr"),
                "asof": f.get("asof") or snap.get("now_ts"),
            }
        state.compute_features = _compute_features
        # equity (플랜 미리보기에 사용)
        mon = snap.get("monitor") or {}
        kpi = mon.get("kpi") or {}
        acct = snap.get("account") or {}
        state.monitor = {"equity": float(kpi.get("equity") or acct.get("totalMarginBalance") or 0.0)}
        # 보조 헬퍼
        state.latency_ms = lambda _sym: 0
        state.now_iso_utc = lambda : time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(snap.get("now_ts",0))/1000))
        state.trade_mode = os.getenv("TRADE_MODE", "testnet")

        # --- 심볼 목록 ---
        syms = getattr(self.bot.bus, "symbols", None) or snap.get("symbols") or sorted(state.marks.keys())

        lines = [f"🧠 실시간 분석 리포트 v2 ({state.now_iso_utc()})  ※ 데이터: live · 트레이딩: {state.trade_mode}"]
        def _status_emoji(level: str) -> str:
            return {"READY":"✅", "CANDIDATE":"🟡", "SCOUT":"🩶"}.get(level,"🩶")

        for sym in syms:
            details = compute_multi_tf(state, sym)
            ticket = synthesize_ticket(details)
            # 최상위 표현용 항목(READY 우선, 점수/확률 보조)
            best = max(details, key=lambda d: (d.readiness.get('level')=="READY", d.score, d.p_up))
            emoji = _status_emoji(best.readiness.get("level"))

            # 요약줄
            lines.append("")
            lines.append(f"{sym} — {emoji} {best.readiness.get('level')} {best.direction} {best.score:+.2f} (p_up {best.p_up:.2f})")

            # 이유(기여 상위), 레짐/변동성
            c = best.contrib; ind = best.ind; gates = best.gates
            rv_txt = "—" if (ind.get("rv_pr") is None) else f"{ind['rv_pr']:.3f}"
            lines.append(f"• 이유: 모멘텀 {c.get('momentum',0):+.2f}, 돌파 {c.get('breakout',0):+.2f}, 평균회귀 {c.get('meanrev',0):+.2f} | 레짐 {best.regime}, RV%tile {rv_txt} {'✅' if all([gates.get('regime_ok'),gates.get('rv_band_ok')]) else '⚠️'}")

            # 계획(진입/사이즈/SL/TP)
            plan = best.plan
            base_ccy = sym.replace("USDT","")
            try_size = float(plan.get("size_qty_est") or 0.0)
            lines.append(f"• 계획: {plan.get('entry','?')} 진입, 크기 ~{try_size:.6f} {base_ccy}(≈${plan.get('notional_est',0):,.0f}, {plan.get('risk_R',0):.2f}R), SL {float(plan.get('sl',0)):.2f}×ATR, TP {','.join(str(x) for x in (plan.get('tp_ladder') or []))}R")

            # 안전장치(게이트 상태)
            lines.append(f"• 안전장치: regime_ok={gates.get('regime_ok')} rv_band_ok={gates.get('rv_band_ok')} risk_ok={gates.get('risk_ok')} cooldown_ok={gates.get('cooldown_ok')}")

            # TF 흐름(가중합)
            from ftm2.analysis.ticket import _vote
            vt = _vote(details)
            lines.append(f"• 신호흐름: {vt['flow']}  (가중합 L={vt['long']} / S={vt['short']})")

            # READY 아니면 보류사유
            blocks = best.readiness.get('blockers', [])
            if best.readiness.get('level') != 'READY' and blocks:
                lines.append(f"• 보류: {', '.join(blocks)}")

            # trace 요약(JSON)
            compact = dict(symbol=sym, readiness=best.readiness.get('level'), score=best.score, gates=best.gates, plan=best.plan)
            lines.append("▼ trace")
            lines.append("```json\n"+json.dumps(compact, ensure_ascii=False)+"\n```")

        return "\n".join(lines)
    # [ANCHOR:ANALYSIS_PUBLISHER] end


    async def _loop(self):
        await self._ensure_message()
        while True:
            try:
                snap = self.bot.bus.snapshot() if hasattr(self.bot,"bus") else {}
                await self._msg.edit(content=self._render(snap))

                self.log.info("[ANALYSIS] 업데이트 완료")
            except Exception as e:
                self.log.warning("[ANALYSIS] 업데이트 오류: %s", e)
            await asyncio.sleep(self.intv)

    def start(self):
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="analysis-pub")
        return self._task

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
