# -*- coding: utf-8 -*-
"""Discord panel slash command — ASCII base name + ko localization, guild sync, View attached."""

# [ANCHOR:DISCORD_PANEL]
import os, inspect
import discord  # type: ignore
from discord import app_commands

try:
    from ftm2.discord_bot.views import ControlPanelView
except Exception:  # pragma: no cover
    from discord_bot.views import ControlPanelView  # type: ignore

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0) or None
GUILD_OBJ = discord.Object(id=GUILD_ID) if GUILD_ID else None

# 기본 이름은 ASCII만! (입력은 /panel, UI 표시는 로컬라이즈)
BASE_NAME = "panel"
BASE_DESC = "Control panel"

# 라이브러리 호환: name_localizations/description_localizations 지원 여부 확인
_SIG = inspect.signature(app_commands.CommandTree.command)
SUPPORTS_LOCALE_KW = (
    "name_localizations" in _SIG.parameters
    and "description_localizations" in _SIG.parameters
)


def setup_panel_commands(bot: discord.Client):
    tree = bot.tree

    if SUPPORTS_LOCALE_KW:
        @tree.command(
            name=BASE_NAME,
            description=BASE_DESC,
            name_localizations={"ko": "패널", "ja": "パネル"},
            description_localizations={"ko": "컨트롤 패널", "ja": "コントロールパネル"},
            guild=GUILD_OBJ,
        )
        async def panel_cmd(interaction: discord.Interaction):
            await interaction.response.send_message(
                "컨트롤 패널(입력은 `/panel`, 표시는 **패널**)",
                view=ControlPanelView(),
                ephemeral=True,
            )
    else:
        # 구버전 호환: locale_str 사용(표시는 로컬라이즈되지만 입력은 /panel)
        NAME = app_commands.locale_str("panel", **{"ko": "패널", "ja": "パネル"})
        DESC = app_commands.locale_str(
            "Control panel", **{"ko": "컨트롤 패널", "ja": "コントロールパネル"}
        )

        @tree.command(name=NAME, description=DESC, guild=GUILD_OBJ)
        async def panel_cmd(interaction: discord.Interaction):
            await interaction.response.send_message(
                "컨트롤 패널(입력은 `/panel`, 표시는 **패널**)",
                view=ControlPanelView(),
                ephemeral=True,
            )

    # 라우팅/상태 점검 커맨드
    @tree.command(name="routes", description="Show channel routing", guild=GUILD_OBJ)
    async def routes_cmd(interaction: discord.Interaction):
        def _get(k):
            return os.getenv(k) or "(unset)"

        text = (
            "**DISCORD ROUTES**\n"
            f"- DASHBOARD: `{_get('DISCORD_CHANNEL_ID_DASHBOARD')}`\n"
            f"- ALERTS   : `{_get('DISCORD_CHANNEL_ID_ALERTS')}`\n"
            f"- ANALYSIS : `{_get('DISCORD_CHANNEL_ID_ANALYSIS')}`\n"
            f"- PANEL    : `{_get('DISCORD_CHANNEL_ID_PANEL')}`\n"
            f"- GUILD    : `{_get('DISCORD_GUILD_ID')}`"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @tree.command(name="status", description="Bot status", guild=GUILD_OBJ)
    async def status_cmd(interaction: discord.Interaction):
        snap = getattr(bot, "bus", None).snapshot() if hasattr(bot, "bus") else {}
        marks = snap.get("marks", {})
        uptime = int((snap.get("now_ts", 0) - snap.get("boot_ts", 0)) / 1000)
        sym = ", ".join(sorted(marks.keys())) or "-"
        await interaction.response.send_message(
            f"**FTM2 상태**\n- 가동: `{uptime}s`\n- 심볼: {sym}", ephemeral=True
        )

    async def _sync():
        try:
            # 길드 명령 즉시 싱크 → 대기 없이 /panel 사용 가능
            if GUILD_OBJ:
                await tree.sync(guild=GUILD_OBJ)
            else:
                await tree.sync()
        except Exception as e:  # pragma: no cover
            if hasattr(bot, "logger"):
                bot.logger.warning("slash sync error: %s", e)

    return _sync

