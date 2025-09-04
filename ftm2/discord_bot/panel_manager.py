# -*- coding: utf-8 -*-
"""Discord Control Panel pinned message manager"""
# [ANCHOR:PANEL_MANAGER]
import json, logging
from pathlib import Path
import discord
from ftm2.utils.env import env_str
from ftm2.discord_bot.views import ControlPanelView

class PanelManager:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.log = logging.getLogger("ftm2.panel")
        self.path = Path("./runtime/panel.json")
        self._msg = None

    def _load_mid(self):
        try:
            if self.path.exists():
                return json.loads(self.path.read_text(encoding="utf-8")).get("mid")
        except Exception:
            pass
        return None

    def _save_mid(self, mid: int):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"mid": mid}), encoding="utf-8")

    async def ensure_panel_message(self):
        ch_id = int(env_str("DISCORD_CHANNEL_ID_PANEL","0") or "0")
        if not ch_id:
            raise RuntimeError("DISCORD_CHANNEL_ID_PANEL not set")
        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)

        mid = self._load_mid()
        content = (
            "🎛️ **컨트롤 패널**\n"
            "• 이 메시지는 고정이며 버튼으로 봇을 제어합니다.\n"
            "• 입력 명령은 `/panel` (표시는 **패널**)"
        )
        msg = None
        if mid:
            try: msg = await ch.fetch_message(mid)
            except Exception: msg = None

        if msg is None:
            msg = await ch.send(content, view=ControlPanelView())
            try: await msg.pin(reason="FTM2 Control Panel")
            except Exception: pass
            self._save_mid(msg.id)
            self.log.info("[PANEL] created mid=%s", msg.id)
        else:
            try: await msg.edit(content=content, view=ControlPanelView())
            except Exception as e: self.log.warning("[PANEL] edit fail: %s", e)
        self._msg = msg
        return msg

    async def refresh(self):
        if self._msg: 
            try: await self._msg.edit(view=ControlPanelView())
            except Exception: pass

    async def close(self):
        # 필요 시 View 해제만. 메시지는 남겨둔다.
        try:
            if self._msg: await self._msg.edit(view=None)
        except Exception: pass
