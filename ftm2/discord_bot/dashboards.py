# -*- coding: utf-8 -*-
"""
대시보드: 주기적으로 편집(update)되는 단일 메시지
- 채널: CHAN_DASHBOARD_ID
- 메시지 ID를 runtime/dashboard_msg.json 에 보관 (재시작 시 재사용)
"""
from __future__ import annotations

import os
import json
import time
import logging
from typing import Dict, Any, Optional

import discord  # type: ignore
from ftm2.utils.env import env_str

log = logging.getLogger("ftm2.dashboard")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DASHBOARD_STORE = os.path.join("runtime", "dashboard_msg.json")


def _load_msg_id() -> Optional[int]:
    try:
        with open(DASHBOARD_STORE, "r", encoding="utf-8") as f:
            return int(json.load(f).get("message_id"))
    except Exception:
        return None


def _save_msg_id(mid: int) -> None:
    os.makedirs(os.path.dirname(DASHBOARD_STORE), exist_ok=True)
    with open(DASHBOARD_STORE, "w", encoding="utf-8") as f:
        json.dump({"message_id": mid}, f)


def _fmt_number(x: float) -> str:
    try:
        return f"{x:,.4f}"
    except Exception:
        return str(x)


def _render_dashboard(snap: Dict[str, Any]) -> str:
    marks = snap.get("marks", {})
    positions = snap.get("positions", {})
    uptime = int((snap.get("now_ts", 0) - snap.get("boot_ts", 0)) / 1000)
    lines = [
        "📊 **실시간 대시보드**",
        f"• 가동 시간: `{uptime}s`",
    ]
    if marks:
        sym_parts = []
        for s, v in marks.items():
            sym_parts.append(f"{s}: **{_fmt_number(v.get('price', 0.0))}**")
        lines.append("• 시세(마크프라이스): " + " | ".join(sym_parts))
    if positions:
        pos_parts = []
        for s, p in positions.items():
            pos_parts.append(
                f"{s}: 수량 `{_fmt_number(p.get('pa', 0.0))}` / 진입가 `{_fmt_number(p.get('ep',0.0))}` / 평가손익 `{_fmt_number(p.get('up',0.0))}`"
            )
        lines.append("• 포지션: " + " | ".join(pos_parts))
    lines.append("\n_※ 본 메시지는 스팸 방지를 위해 **편집(update)** 방식으로 갱신됩니다._")
    return "\n".join(lines)


class DashboardManager:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.log = log
        self._msg: Optional[discord.Message] = None

    def _load_mid(self) -> Optional[int]:
        return _load_msg_id()

    def _save_mid(self, mid: int) -> None:
        _save_msg_id(mid)

    async def ensure_dashboard_message(self) -> discord.Message:
        ch_id = int(env_str("DISCORD_CHANNEL_ID_DASHBOARD", "0") or "0")
        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)

        mid = self._load_mid()
        msg: Optional[discord.Message] = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)  # type: ignore
                self.log.info("[대시보드] 기존 메시지 재사용(mid=%s)", mid)
            except Exception:
                msg = None

        if msg is None:
            init_text = "📊 **FTM2 KPI 대시보드** (초기화 중)"
            msg = await ch.send(init_text)
            try:
                await msg.pin(reason="FTM2 Dashboard")
            except Exception:
                pass
            self._save_mid(msg.id)
            self.log.info("[대시보드] 신규 생성(mid=%s)", msg.id)

        self._msg = msg
        return msg

    async def update(self, snapshot: Dict[str, Any]) -> None:
        if not self._msg:
            return
        content = _render_dashboard(snapshot)
        await self._msg.edit(content=content)
        log.info("[대시보드] 업데이트 완료")
