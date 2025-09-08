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

    # [ANCHOR:ANALYSIS_PUBLISHER] begin
    def _render(self, snap: dict) -> str:
        import math, os, time
        marks: dict = snap.get("marks", {}) or {}
        feats: dict = snap.get("features", {}) or {}
        regimes: dict = snap.get("regimes", {}) or {}
        fcs: dict = snap.get("forecasts", {}) or {}
        syms = snap.get("symbols") or sorted(marks.keys()) or ["BTCUSDT","ETHUSDT"]
        t = time.strftime("%H:%M:%S", time.gmtime(int(snap.get("now_ts", 0))/1000))
        lines = [f"🧠 실시간 분석 리포트 ({t} UTC)"]

        tfs = ("5m","15m","1h","4h")
        arrow = {"LONG":"⬆","SHORT":"⬇","FLAT":"→"}

        def _feat_snip(s, tf):
            d = feats.get((s, tf)) or {}
            emaf = float(d.get("ema_fast",0.0)); emas = float(d.get("ema_slow",1e-12)) or 1e-12
            ema_spread = (emaf - emas) / (emas if emas != 0.0 else 1e-12)
            rv_pr = float(d.get("rv_pr", 0.0))
            atr = float(d.get("atr14", 0.0))
            return f"ema={ema_spread:+.5f}  rv%={rv_pr:.3f}  atr={atr:.2f}"

        def _comp_snip(fc):
            ex = (fc or {}).get("explain") or {}
            return ("모멘텀:{:+.2f}  평균회귀:{:+.2f}  돌파:{:+.2f}"
                    .format(float(ex.get('mom',0.0)), float(ex.get('meanrev',0.0)), float(ex.get('breakout',0.0))))

        for s in syms:
            parts = []
            for tf in tfs:
                fc = fcs.get((s, tf)) or {}
                rcode = (regimes.get((s, tf)) or {}).get("code","")
                sc = float(fc.get("score",0.0))
                pup = float(fc.get("prob_up") or fc.get("p_up") or 0.5)
                stance = (fc.get("stance") or "FLAT").upper()
                em = arrow.get(stance,"→")
                parts.append(f"{tf}: {sc:+.2f}({em}, r={rcode}, p_up={pup:.2f})")
            lines.append(f"• {s} — " + " | ".join(parts))
            # 가장 짧은 TF 기준으로 특성/기여 표기
            fc0 = fcs.get((s, tfs[0])) or {}
            lines.append("  - 특성: " + _feat_snip(s, tfs[0]))
            lines.append("  - 기여도: " + _comp_snip(fc0))

        # 모드 푸터 동적 반영
        dm = (os.getenv("DATA_MODE") or "live").lower()
        tm = (os.getenv("TRADE_MODE") or "auto").lower()
        lines.append(f"※ 데이터: {dm}, 트레이딩: {tm}")
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
