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
        self.channel_id = int(os.getenv("CHAN_DASHBOARD_ID") or "0")
        self._msg: Optional[discord.Message] = None

    async def ensure_dashboard_message(self) -> None:
        if not self.channel_id:
            log.warning("[대시보드] CHAN_DASHBOARD_ID 가 비어 있습니다.")
            return
        ch = self.bot.get_channel(self.channel_id)
        if ch is None:
            ch = await self.bot.fetch_channel(self.channel_id)

        # 기존 메시지 재사용 시도
        mid = _load_msg_id()
        if mid:
            try:
                msg = await ch.fetch_message(mid)  # type: ignore
                self._msg = msg
                log.info("[대시보드] 기존 메시지 재사용(mid=%s)", mid)
                return
            except Exception:
                pass

        # 신규 생성
        msg = await ch.send("대시보드를 초기화하는 중입니다…")
        self._msg = msg
        _save_msg_id(msg.id)
        await msg.edit(content="📊 **실시간 대시보드**\n초기화 완료. 곧 데이터가 표시됩니다.")

    async def update(self, snapshot: Dict[str, Any]) -> None:
        if not self._msg:
            return
        content = _render_dashboard(snapshot)
        await self._msg.edit(content=content)
        log.info("[대시보드] 업데이트 완료")
