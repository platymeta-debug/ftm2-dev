# -*- coding: utf-8 -*-
"""Discord 실시간 분석 리포트 발행"""
# [ANCHOR:ANALYSIS_PUB]

import json, time, asyncio
from pathlib import Path
import discord, logging
from ftm2.utils.env import env_str, env_int

class AnalysisPublisher:
    def __init__(self, bot, bus, interval_s: int|None=None):
        self.bot = bot
        self.bus = bus
        self.intv = interval_s or env_int("ANALYSIS_REPORT_SEC", 60)
        self.log = logging.getLogger("ftm2.analysis")
        self._path = Path("./runtime/analysis.json")
        self._msg = None
        self._task = None

    async def _ensure_msg(self):
        ch_id = int(env_str("DISCORD_CHANNEL_ID_ANALYSIS","0") or "0")
        if not ch_id: raise RuntimeError("DISCORD_CHANNEL_ID_ANALYSIS not set")
        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)

        mid = None
        if self._path.exists():
            try: mid = json.loads(self._path.read_text()).get("mid")
            except Exception: pass

        if mid:
            try: self._msg = await ch.fetch_message(mid)
            except Exception: self._msg = None

        if self._msg is None:
            self._msg = await ch.send("🔎 분석 초기화…")
            try: await self._msg.pin(reason="FTM2 Analysis")
            except Exception: pass
            self._path.write_text(json.dumps({"mid": self._msg.id}))
        return self._msg

    def _render(self, snap: dict) -> str:
        marks: dict = snap.get("marks", {}) or {}
        syms = snap.get("symbols") or sorted(marks.keys())
        regimes = snap.get("regimes", {}) or {}

        # 점수/방향을 어디서든 찾아본다
        sig_root = snap.get("signals", {}) or snap.get("forecast", {}) or snap.get("intents", {}) or {}

        def _sig(sym):
            # sym 단일 dict 또는 TF별 dict 모두 허용
            s = sig_root.get(sym, {})
            # 흔한 키들 후보
            score = s.get("score") or s.get("s") or s.get("strength") or s.get("v") or None
            side  = s.get("side")  or s.get("dir") or s.get("intent") or None
            # TF별 구조면 대표 TF 하나 집계
            if score is None and isinstance(s, dict):
                for v in s.values():
                    if isinstance(v, dict):
                        score = v.get("score") or v.get("s") or score
                        side  = v.get("side") or v.get("dir") or side
            return score, side

        t = time.strftime("%H:%M:%S", time.gmtime(int(snap.get("now_ts",0))/1000))
        lines = [f"🧠 **실시간 분석 리포트** (`{t} UTC`)"]

        label = {"TREND_UP":"📈상승","TREND_DOWN":"📉하락",
                 "RANGE_HIGH":"🟧박스(고변동)","RANGE_LOW":"🟦박스(저변동)"}

        for s in syms:
            price = marks.get(s)
            ptxt = f"{price:,.2f}" if isinstance(price,(int,float)) else "-"
            rmap = regimes.get(s, {})
            tfpart = " | ".join(f"{tf}:{label.get(rmap.get(tf),'·')}" for tf in ("5m","15m","1h","4h"))
            sc, side = _sig(s)
            sc_txt = f"{sc:+.2f}" if isinstance(sc,(int,float)) else "—"
            side_txt = side or "—"
            lines.append(f"• **{s}** {ptxt} — {tfpart} | 점수:{sc_txt} / 방향:{side_txt}")

        lines.append("_※ 데이터: live, 트레이딩: testnet_")
        return "\n".join(lines)


    async def _loop(self):
        await self._ensure_msg()
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

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
