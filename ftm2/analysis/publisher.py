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
        import time, os
        marks = snap.get("marks", {}) or {}
        feats = snap.get("features", {}) or {}
        regimes = snap.get("regimes", {}) or {}
        fcs = snap.get("forecasts", {}) or {}
        syms = snap.get("symbols") or sorted(marks.keys()) or ["BTCUSDT", "ETHUSDT"]
        tfs = ("5m", "15m", "1h", "4h")
        t = time.strftime("%H:%M:%S", time.gmtime(int(snap.get("now_ts", 0)) / 1000))
        lines = [f"🧠 실시간 분석 리포트 ({t} UTC)"]

        def feat(s: str, tf: str):
            f = feats.get((s, tf)) or {}
            r = regimes.get((s, tf)) or {}
            ema = float(r.get("ema", 0.0))
            rv20 = float(f.get("rv20", 0.0))
            atr = float(f.get("atr", 0.0))
            ret1 = float(f.get("ret1", 0.0)) * 100.0
            rv_pct = float(r.get("rv_pr", 0.0))
            return ema, rv20, atr, ret1, rv_pct

        for s in syms:
            lines.append(f"[{s}]")
            for tf in tfs:
                fc = fcs.get((s, tf)) or {}
                sc = float(fc.get("score", 0.0))
                pup = float(fc.get("p_up") or fc.get("prob_up") or 0.5)
                stance = (fc.get("stance") or "FLAT").upper()
                rcode = (regimes.get((s, tf)) or {}).get("code", "")
                arrow = "⬆" if stance == "LONG" else ("⬇" if stance == "SHORT" else "→")
                lines.append(f"  {tf:<3}  점수 {sc:+.2f} | 방향 {arrow} | p_up {pup:.2f} | 레짐 {rcode}")
                ema, rv20, atr, ret1, rv_pct = feat(s, tf)
                lines.append(
                    f"      지표: EMA {ema:+.5f} | RV20 {rv20:.2%} | ATR {atr:.2f} | RET1 {ret1:+.3f}% | RV%tile {rv_pct:.3f}"
                )
                ex = (fc.get("explain") or {})
                lines.append(
                    f"      기여도: 모멘텀 {float(ex.get('mom',0.0)):+.2f} / 평균회귀 {float(ex.get('meanrev',0.0)):+.2f} / 돌파 {float(ex.get('breakout',0.0)):+.2f}"
                )
            lines.append("")

        dm = (os.getenv("DATA_MODE") or "live").lower()
        tm = (os.getenv("TRADE_MODE") or "testnet").lower()
        lines.append(f"※ 데이터: {dm} | 트레이딩: {tm}")

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
