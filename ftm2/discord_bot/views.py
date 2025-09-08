"""Discord panel view and helpers."""
# [ANCHOR:PANEL_VIEWS] begin
import logging

import discord  # type: ignore

log = logging.getLogger(__name__)


# [ANCHOR:DISCORD_EXEC_TOGGLE] begin
async def apply_exec_toggle(bus, active: bool, orchestrator=None):

    if not hasattr(bus, "config") or not isinstance(bus.config, dict):
        bus.config = {}
    bus.config["exec_active"] = bool(active)


    try:
        if orchestrator and hasattr(orchestrator, "exec_router"):
            orchestrator.exec_router.cfg.active = bool(active)
        if hasattr(orchestrator, "log"):
            orchestrator.log.info(f"[CTRL] EXEC_ACTIVE -> {bool(active)}")
    except Exception as e:
        if hasattr(orchestrator, "log"):
            orchestrator.log.warning(f"[CTRL][WARN] set exec_active failed: {e}")

# [ANCHOR:DISCORD_EXEC_TOGGLE] end



try:
    class ControlPanelView(discord.ui.View):
        """컨트롤패널 버튼 뷰(고정 메시지용)."""
        def __init__(self, bus, orchestrator=None):
            super().__init__(timeout=None)
            self.bus = bus
            self.orch = orchestrator

        @discord.ui.button(label="자동 매매 ON",
                           style=discord.ButtonStyle.success,
                           custom_id="ftm2:exec:on")
        async def btn_on(self, interaction: "discord.Interaction", button: "discord.ui.Button"):
            await apply_exec_toggle(self.bus, True, orchestrator=self.orch)

            await interaction.response.send_message("✅ 자동 매매: ON", ephemeral=True)


        @discord.ui.button(label="자동 매매 OFF",
                           style=discord.ButtonStyle.danger,
                           custom_id="ftm2:exec:off")
        async def btn_off(self, interaction: "discord.Interaction", button: "discord.ui.Button"):
            await apply_exec_toggle(self.bus, False, orchestrator=self.orch)

            await interaction.response.send_message("⛔ 자동 매매: OFF", ephemeral=True)

except Exception:
    pass
# [ANCHOR:PANEL_VIEWS] end
