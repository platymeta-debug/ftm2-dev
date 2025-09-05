"""Discord panel view and helpers."""
# [ANCHOR:PANEL_VIEWS] begin
import logging

import discord  # type: ignore

log = logging.getLogger(__name__)


# [ANCHOR:PANEL_TOGGLE_SAFE] begin
async def apply_exec_toggle(bus, active: bool, *, orchestrator=None):
    # StateBus 보장
    if not hasattr(bus, "config"):
        class _Cfg: pass
        bus.config = _Cfg()
    prev = getattr(bus.config, "exec_active", None)
    bus.config.exec_active = bool(active)

    # 오케스트레이터에게 알림(있으면)
    if orchestrator and hasattr(orchestrator, "on_exec_toggle"):
        try:
            await orchestrator.on_exec_toggle(bool(active))
        except Exception:
            log.exception("E_ORCH_TOGGLE_CB")

    # (선택) DB upsert는 기존 유틸 사용
    try:
        from ftm2.panel import _db_upsert_exec_active
        _db_upsert_exec_active(bool(active))
    except Exception:
        pass

    log.info("[EXEC] %s (source=PANEL, prev=%s)",
             "enabled" if active else "disabled", prev)
# [ANCHOR:PANEL_TOGGLE_SAFE] end

    try:
        router_active = getattr(getattr(getattr(bus, "exec_router", None), "cfg", None), "active", None)
        if router_active != active and hasattr(bus, "exec_router"):
            bus.exec_router.cfg.active = bool(active)
            log.info("[EXEC_ROUTE] sync router active -> %s", active)
    except Exception:
        log.exception("E_EXEC_ROUTE_SYNC")


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
            await interaction.response.send_message("✅ 자동 매매: **ON**", ephemeral=True)

        @discord.ui.button(label="자동 매매 OFF",
                           style=discord.ButtonStyle.danger,
                           custom_id="ftm2:exec:off")
        async def btn_off(self, interaction: "discord.Interaction", button: "discord.ui.Button"):
            await apply_exec_toggle(self.bus, False, orchestrator=self.orch)
            await interaction.response.send_message("🛑 자동 매매: **OFF**", ephemeral=True)
except Exception:
    pass
# [ANCHOR:PANEL_VIEWS] end
