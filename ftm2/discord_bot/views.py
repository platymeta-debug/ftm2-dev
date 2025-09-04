# -*- coding: utf-8 -*-
# [ANCHOR:PANEL_VIEWS] begin
import os, sqlite3, logging, contextlib
import discord  # type: ignore

log = logging.getLogger(__name__)


def _db_path() -> str:
    return os.getenv("DB_PATH", "./runtime/trader.db")


def _db_upsert_exec_active(active: bool):
    """config 테이블에 EXEC_ACTIVE upsert."""
    try:
        with sqlite3.connect(_db_path(), timeout=2) as conn:
            conn.execute(
                """
            CREATE TABLE IF NOT EXISTS config(
              key TEXT PRIMARY KEY,
              value TEXT,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
            )
            conn.execute(
                """
            INSERT INTO config(key, value, updated_at)
            VALUES ('EXEC_ACTIVE', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=CURRENT_TIMESTAMP
            """,
                ("1" if active else "0",),
            )
        log.info("[PANEL] EXEC_ACTIVE upserted=%s path=%s", active, _db_path())
    except Exception as e:
        log.warning("E_DB_UPSERT_EXEC_ACTIVE %r", e)


async def apply_exec_toggle(bus, active: bool, *, source="PANEL", orchestrator=None):
    """버튼/명령으로 토글 시 StateBus + DB 반영(+오케스트레이터 알림)."""
    prev = getattr(getattr(bus, "config", object()), "exec_active", None)
    if not hasattr(bus, "config"):
        class _Cfg: ...
        bus.config = _Cfg()
    bus.config.exec_active = bool(active)

    if orchestrator and hasattr(orchestrator, "on_exec_toggle"):
        try:
            await orchestrator.on_exec_toggle(bool(active))
        except Exception:
            log.exception("E_ORCH_TOGGLE_CB")

    _db_upsert_exec_active(bool(active))
    log.info("[EXEC] %s (source=%s, prev=%s)",
             "enabled" if active else "disabled", source, prev)


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
            await apply_exec_toggle(self.bus, True, source="PANEL", orchestrator=self.orch)
            await interaction.response.send_message("✅ 자동 매매: **ON**", ephemeral=True)

        @discord.ui.button(label="자동 매매 OFF",
                           style=discord.ButtonStyle.danger,
                           custom_id="ftm2:exec:off")
        async def btn_off(self, interaction: "discord.Interaction", button: "discord.ui.Button"):
            await apply_exec_toggle(self.bus, False, source="PANEL", orchestrator=self.orch)
            await interaction.response.send_message("🛑 자동 매매: **OFF**", ephemeral=True)
except Exception:
    pass
# [ANCHOR:PANEL_VIEWS] end
