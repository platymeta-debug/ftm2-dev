# -*- coding: utf-8 -*-
"""Discord 실시간 분석 리포트 발행"""
# [ANCHOR:ANALYSIS_PUB]
import os, json
from pathlib import Path
import logging, asyncio, math, time
import discord  # type: ignore


class AnalysisPublisher:
    def __init__(self, bot, bus, interval_s=60):
        self.bot = bot
        self.bus = bus
        self.intv = interval_s
        self.log = logging.getLogger("ftm2.analysis")
        self._task = None
        self._path = Path("./runtime/analysis.json")
        self._msg = None

    async def _ensure_msg(self):
        ch_id = int(os.getenv("DISCORD_CHANNEL_ID_ANALYSIS", "0") or 0)
        if not ch_id:
            raise RuntimeError("DISCORD_CHANNEL_ID_ANALYSIS not set")
        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
        mid = None
        if self._path.exists():
            try:
                mid = json.loads(self._path.read_text()).get("mid")
            except Exception:
                pass
        if mid:
            try:
                self._msg = await ch.fetch_message(mid)
                return self._msg
            except Exception:
                self._msg = None
        self._msg = await ch.send("🔎 분석 초기화 중…")
        try:
            await self._msg.pin(reason="FTM2 Analysis")
        except Exception:
            pass
        self._path.write_text(json.dumps({"mid": self._msg.id}))
        return self._msg

    def _fmt_row(self, sym, snap, tfs=("5m","15m","1h","4h")):
        marks = snap.get("marks", {})
        regimes = snap.get("regimes", {}).get(sym, {})  # ex) {"5m":"RANGE_HIGH",...}
        m = marks.get(sym)
        parts = [f"**{sym}** {m:.4f}" if isinstance(m,(int,float)) else f"**{sym}** -"]
        label = {"TREND_UP":"📈상승","TREND_DOWN":"📉하락",
                 "RANGE_HIGH":"🟧박스(고변동)","RANGE_LOW":"🟦박스(저변동)","NONE":"·"}
        for tf in tfs:
            r = regimes.get(tf) or "NONE"
            parts.append(f"{tf}:{label.get(r,r)}")
        return "  • " + " | ".join(parts)

    def _render(self, snap):
        syms = snap.get("symbols", [])
        t = time.strftime("%H:%M:%S", time.gmtime(int(snap.get("now_ts",0))/1000))
        hdr = f"🧠 **실시간 분석 리포트**  (`{t} UTC`)\n"
        body = "\n".join(self._fmt_row(s, snap) for s in syms)
        note = "\n_※ 레짐은 실선물 마크/클라인 기반. 트레이딩은 TESTNET 모드._"
        return hdr + body + note

    async def _loop(self):
        await self._ensure_msg()
        while True:
            try:
                snap = self.bot.bus.snapshot() if hasattr(self.bot,"bus") else {}
                txt = self._render(snap)
                await self._msg.edit(content=txt)
                self.log.info("[ANALYSIS] 업데이트 완료")
            except Exception as e:
                self.log.warning("[ANALYSIS] 업데이트 오류: %s", e)
            await asyncio.sleep(self.intv)

    def start(self):
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="analysis-pub")

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
