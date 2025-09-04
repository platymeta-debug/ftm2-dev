# -*- coding: utf-8 -*-
"""Discord panel slash command — ASCII base name + ko localization, guild sync, View attached."""

# [ANCHOR:DISCORD_PANEL]
import inspect, discord
from discord import app_commands
from ftm2.utils.env import env_str
from ftm2.discord_bot.views import ControlPanelView

GUILD_ID = int(env_str("DISCORD_GUILD_ID","0") or "0") or None
GUILD_OBJ = discord.Object(id=GUILD_ID) if GUILD_ID else None

SIG = inspect.signature(app_commands.CommandTree.command)
HAS_LOCALE_KW = ("name_localizations" in SIG.parameters)

def setup_panel_commands(bot: discord.Client):
    tree = bot.tree
    if HAS_LOCALE_KW:
        @tree.command(
            name="panel", description="Control panel",
            name_localizations={"ko":"패널"},
            description_localizations={"ko":"컨트롤 패널"},
            guild=GUILD_OBJ)
        async def panel_cmd(ia: discord.Interaction):
            await ia.response.send_message("컨트롤 패널", view=ControlPanelView(), ephemeral=False)
    else:
        NAME = app_commands.locale_str("panel", **{"ko":"패널"})
        DESC = app_commands.locale_str("Control panel", **{"ko":"컨트롤 패널"})
        @tree.command(name=NAME, description=DESC, guild=GUILD_OBJ)
        async def panel_cmd(ia: discord.Interaction):
            await ia.response.send_message("컨트롤 패널", view=ControlPanelView(), ephemeral=False)

    @tree.command(name="routes", description="Show channel routing", guild=GUILD_OBJ)
    async def routes_cmd(ia: discord.Interaction):
        def g(k): return env_str(k,"(unset)")
        txt = ("**DISCORD ROUTES**\n"
               f"- DASHBOARD: `{g('DISCORD_CHANNEL_ID_DASHBOARD')}`\n"
               f"- ALERTS   : `{g('DISCORD_CHANNEL_ID_ALERTS')}`\n"
               f"- ANALYSIS : `{g('DISCORD_CHANNEL_ID_ANALYSIS')}`\n"
               f"- PANEL    : `{g('DISCORD_CHANNEL_ID_PANEL')}`\n"
               f"- GUILD    : `{g('DISCORD_GUILD_ID')}`")
        await ia.response.send_message(txt, ephemeral=True)

    async def _sync():
        if GUILD_OBJ: await tree.sync(guild=GUILD_OBJ)
        else:         await tree.sync()
    return _sync
