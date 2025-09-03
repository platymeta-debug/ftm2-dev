# -*- coding: utf-8 -*-
"""
슬래시 명령(한글 현지화) & 패널 메시지
- Discord 제한상 command 이름은 영문으로 두되, name_localizations={"ko": "…"} 로 **한글 노출**
"""
from __future__ import annotations

import os
import logging
import discord  # type: ignore
from discord import app_commands  # type: ignore
from discord.ext import commands  # type: ignore

try:
    from ftm2.discord_bot.views import ControlPanelView
except Exception:  # pragma: no cover
    from discord_bot.views import ControlPanelView  # type: ignore

log = logging.getLogger("ftm2.panel")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _panel_channel(bot: commands.Bot) -> discord.TextChannel | None:
    panel_id = int(os.getenv("CHAN_PANEL_ID") or "0")
    ch = bot.get_channel(panel_id) if panel_id else None
    return ch if isinstance(ch, discord.TextChannel) else None


def setup_panel_commands(bot: commands.Bot) -> None:
    tree = bot.tree

    # /mode → '모드' 로 한글 노출
    @tree.command(
        name="mode",
        description="실행 모드를 전환합니다.",
        name_localizations={"ko": "모드"},
        description_localizations={"ko": "실행 모드를 전환합니다. (paper/testnet/live)"},
    )
    @app_commands.describe(kind="모드를 선택하세요: paper/testnet/live")
    @app_commands.rename(kind="모드")
    async def mode(inter: discord.Interaction, kind: str):
        # 실제 변경은 Orchestrator와의 계약 필요(M3/M4)
        await inter.response.send_message(f"⚙️ 모드 전환 요청: `{kind}` (스켈레톤 — 추후 반영)", ephemeral=True)

    # /auto → '자동'
    @tree.command(
        name="auto",
        description="자동 매매를 켜거나 끕니다.",
        name_localizations={"ko": "자동"},
        description_localizations={"ko": "자동 매매를 켜거나 끕니다."},
    )
    @app_commands.describe(state="상태 선택: on/off")
    @app_commands.rename(state="상태")
    async def auto(inter: discord.Interaction, state: str):
        await inter.response.send_message(f"🔄 자동 매매: `{state}` (스켈레톤)", ephemeral=True)

    # /close → '청산'
    @tree.command(
        name="close",
        description="포지션을 청산합니다.",
        name_localizations={"ko": "청산"},
        description_localizations={"ko": "포지션을 청산합니다. (all/BTC/ETH)"},
    )
    @app_commands.describe(scope="대상: all/BTC/ETH")
    @app_commands.rename(scope="대상")
    async def close(inter: discord.Interaction, scope: str):
        await inter.response.send_message(f"🧹 청산 요청: `{scope}` (스켈레톤)", ephemeral=True)

    # /reverse → '반전'
    @tree.command(
        name="reverse",
        description="해당 심볼 포지션을 반전합니다.",
        name_localizations={"ko": "반전"},
        description_localizations={"ko": "해당 심볼 포지션을 반전합니다."},
    )
    async def reverse(inter: discord.Interaction, symbol: str):
        await inter.response.send_message(f"🔁 반전 요청: `{symbol}` (스켈레톤)", ephemeral=True)

    # /flat → '평탄'
    @tree.command(
        name="flat",
        description="해당 심볼 포지션을 0으로 만듭니다.",
        name_localizations={"ko": "평탄"},
        description_localizations={"ko": "해당 심볼 포지션을 0으로 만듭니다."},
    )
    async def flat(inter: discord.Interaction, symbol: str):
        await inter.response.send_message(f"🧊 평탄 요청: `{symbol}` (스켈레톤)", ephemeral=True)

    # 컨트롤 패널 메시지(버튼 포함) — 한글
    @tree.command(
        name="panel",
        description="컨트롤 패널 메시지를 채널에 게시합니다.",
        name_localizations={"ko": "패널"},
        description_localizations={"ko": "컨트롤 패널 메시지를 채널에 게시합니다."},
    )
    async def panel_cmd(inter: discord.Interaction):
        ch = _panel_channel(bot)
        if ch is None:
            await inter.response.send_message("⚠️ 패널 채널(CHAN_PANEL_ID)이 설정되지 않았습니다.", ephemeral=True)
            return
        await ch.send("🛠️ **컨트롤 패널** — 아래 버튼으로 자동 매매를 제어하세요.", view=ControlPanelView())
        await inter.response.send_message("✅ 패널을 게시했습니다.", ephemeral=True)

    # 간단 알림 테스트 — 한글
    @tree.command(
        name="alert",
        description="알림 채널로 테스트 메시지를 보냅니다.",
        name_localizations={"ko": "알림"},
        description_localizations={"ko": "알림 채널로 테스트 메시지를 보냅니다."},
    )
    async def alert_cmd(inter: discord.Interaction, 내용: str):
        alert_id = int(os.getenv("CHAN_ALERTS_ID") or "0")
        ch = bot.get_channel(alert_id) if alert_id else None
        if isinstance(ch, discord.TextChannel):
            await ch.send(f"🔔 **알림**: {내용}")
            await inter.response.send_message("✅ 전송 완료", ephemeral=True)
        else:
            await inter.response.send_message("⚠️ 알림 채널(CHAN_ALERTS_ID)이 설정되지 않았습니다.", ephemeral=True)
