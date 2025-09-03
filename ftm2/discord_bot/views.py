# -*- coding: utf-8 -*-
"""
컨트롤 패널용 기본 버튼 View (스켈레톤)
- 실제 자동매매 토글/모드 변경은 M3/M4에서 Orchestrator와 연결 예정
"""
from __future__ import annotations
import logging
import discord  # type: ignore

log = logging.getLogger("ftm2.views")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class ControlPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="자동 매매 ON", style=discord.ButtonStyle.success, custom_id="auto_on")
    async def auto_on(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 자동 매매: **ON** (스켈레톤 — 실제 반영은 추후 티켓에서 연결됩니다)", ephemeral=True)

    @discord.ui.button(label="자동 매매 OFF", style=discord.ButtonStyle.danger, custom_id="auto_off")
    async def auto_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⛔ 자동 매매: **OFF** (스켈레톤 — 실제 반영은 추후 티켓에서 연결됩니다)", ephemeral=True)
