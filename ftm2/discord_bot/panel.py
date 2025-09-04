# -*- coding: utf-8 -*-
"""Discord panel slash command with locale_str for compatibility."""

# [ANCHOR:DISCORD_PANEL]
from discord import app_commands
import discord, os

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0) or None
GUILD_OBJ = discord.Object(id=GUILD_ID) if GUILD_ID else None

NAME = app_commands.locale_str("패널", **{"en-US": "panel", "ja": "パネル"})
DESC = app_commands.locale_str(
    "컨트롤 패널", **{"en-US": "Control panel", "ja": "コントロールパネル"}
)


def setup_panel_commands(bot: discord.Client):
    tree = bot.tree

    @tree.command(name=NAME, description=DESC, guild=GUILD_OBJ)
    async def panel(interaction: discord.Interaction):
        await interaction.response.send_message("패널 ready", ephemeral=True)

    async def _sync():
        try:
            if GUILD_OBJ:
                await tree.sync(guild=GUILD_OBJ)
            else:
                await tree.sync()
        except Exception as e:  # pragma: no cover
            if hasattr(bot, "logger"):
                bot.logger.warning("slash sync error: %s", e)

    return _sync

