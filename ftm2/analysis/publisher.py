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
        marks: dict = snap.get("marks", {}) or {}
        syms = getattr(self.bot.bus, "symbols", None) or snap.get("symbols") or sorted(marks.keys())
        regimes = snap.get("regimes", {}) or {}
        intents = snap.get("intents", {}) or snap.get("forecast", {}) or snap.get("signals", {}) or {}
        t = time.strftime("%H:%M:%S", time.gmtime(int(snap.get("now_ts", 0)) / 1000))
        lines = [f"🧠 실시간 분석 리포트 ({t} UTC)"]
        arrow = {"LONG": "⬆", "SHORT": "⬇", "FLAT": "→"}
        tfs = ("5m", "15m", "1h", "4h")
        for s in syms:
            intent = intents.get(s, {})
            score = float(intent.get("score", 0.0)) if isinstance(intent, dict) else 0.0
            direction = intent.get("dir") or intent.get("stance") or intent.get("side") or "FLAT"
            em = arrow.get(str(direction).upper(), "→")
            dots = []
            for tf in tfs:
                dot = "●" if regimes.get((s, tf)) or intent.get(tf) else "·"
                dots.append(f"{tf}:{dot}")
            lines.append(
                f"• {s} — {' | '.join(dots)} | 점수:{score:+.1f} / 방향:{em}"
            )
        lines.append("※ 데이터: live, 트레이딩: testnet")
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
