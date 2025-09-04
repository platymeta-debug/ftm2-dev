# -*- coding: utf-8 -*-
"""Discord Control Panel pinned message manager"""
# [ANCHOR:PANEL_MANAGER]
import json, os, logging
from pathlib import Path
import discord  # type: ignore

try:
    from ftm2.discord_bot.views import ControlPanelView
except Exception:  # pragma: no cover
    from discord_bot.views import ControlPanelView  # type: ignore


class PanelManager:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.log = logging.getLogger("ftm2.panel")
        self.path = Path("./runtime/panel.json")
        self._msg = None

    def _load_mid(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8")).get("mid")
            except Exception:
                pass
        return None

    def _save_mid(self, mid: int):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"mid": mid}), encoding="utf-8")

    async def ensure_panel_message(self):
        ch_id = int(os.getenv("DISCORD_CHANNEL_ID_PANEL", "0") or 0)
        if not ch_id:
            raise RuntimeError("DISCORD_CHANNEL_ID_PANEL not set")
        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)

        mid = self._load_mid()
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
                self.log.info("[PANEL] 기존 메시지 재사용(mid=%s)", mid)
            except Exception:
                msg = None

        content = (
            "🎛️ **컨트롤 패널**\n"
            "• 이 메시지는 고정이며 버튼으로 봇을 제어합니다.\n"
            "• (입력 명령은 `/panel` 입니다. 표시 명칭은 **패널**)"
        )

        if msg is None:
            msg = await ch.send(content, view=ControlPanelView())
            try:
                await msg.pin(reason="FTM2 Control Panel")
            except Exception:
                pass
            self._save_mid(msg.id)
            self.log.info("[PANEL] 신규 생성(mid=%s)", msg.id)
        else:
            try:
                await msg.edit(content=content, view=ControlPanelView())
            except Exception as e:
                self.log.warning("[PANEL] edit 실패: %s", e)

        self._msg = msg
        return msg

    async def refresh(self):
        if self._msg is None:
            await self.ensure_panel_message()
        else:
            try:
                await self._msg.edit(view=ControlPanelView())
            except Exception as e:
                self.log.warning("[PANEL] refresh 실패: %s", e)

    async def close(self):
        # 메시지는 남겨두고 View만 떼면 된다(원한다면 pass)
        try:
            if self._msg:
                await self._msg.edit(view=None)
        except Exception:
            pass
