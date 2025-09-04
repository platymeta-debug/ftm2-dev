# -*- coding: utf-8 -*-
"""
Discord Bot Runner (스켈레톤)
- 한글 패널/대시보드/알림
- 대시보드 15초 주기 편집 업데이트
"""
from __future__ import annotations

import os
import asyncio
import logging
from typing import Optional

try:
    import discord  # type: ignore
    from discord.ext import tasks, commands  # type: ignore
except Exception:  # pragma: no cover - discord 미설치 시
    discord = None  # type: ignore
    tasks = None  # type: ignore
    commands = None  # type: ignore

try:
    from ftm2.core.state import StateBus
except Exception:  # pragma: no cover
    from core.state import StateBus  # type: ignore



log = logging.getLogger("ftm2.discord")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


if discord is None:  # pragma: no cover - discord.py 미설치 환경
    def run_discord_bot(bus: StateBus) -> None:
        """discord.py 미설치 시 단순 경고만 남기고 종료."""
        log.warning("🔒 Discord 라이브러리가 설치되지 않았습니다. Discord 기능은 비활성화됩니다.")
        return
else:
    # 이 패키지/경로 배치 차이를 흡수 (둘 중 가능한 쪽 사용)
    try:
        from ftm2.dashboard import (
            ensure_dashboard_message,
            update_dashboard,
            render_dashboard,
        )
        from ftm2.discord_bot.panel import setup_panel_commands
        from ftm2.discord_bot.panel_manager import PanelManager
        from ftm2.analysis.publisher import AnalysisPublisher

    except Exception:  # pragma: no cover
        from dashboard import (  # type: ignore
            ensure_dashboard_message,  # type: ignore
            update_dashboard,  # type: ignore
            render_dashboard,  # type: ignore
        )
        from discord_bot.panel import setup_panel_commands  # type: ignore
        from discord_bot.panel_manager import PanelManager  # type: ignore
        from analysis.publisher import AnalysisPublisher  # type: ignore


    # [ANCHOR:DISCORD_TASKS] begin
    class TaskRegistryMixin:
        """봇 내부에서 생성한 백그라운드 태스크를 한 곳에서 관리/종료."""
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._bg_tasks: set[asyncio.Task] = set()

        def add_bg_task(self, coro, name: str):
            if asyncio.iscoroutine(coro):
                t = asyncio.create_task(coro, name=name)
            elif isinstance(coro, asyncio.Task):
                t = coro
                t.set_name(name)
            else:
                raise TypeError("add_bg_task expects coroutine or Task")
            self._bg_tasks.add(t)

            def _done(tt: asyncio.Task):
                self._bg_tasks.discard(tt)
                try:
                    exc = tt.exception()
                except asyncio.CancelledError:
                    log.info("E_DISCORD_TASK_CANCELLED name=%s", tt.get_name())
                    return
                except Exception:
                    log.exception("E_TASK_DONE_CB")
                    return
                if exc:
                    log.warning("E_TASK_FAIL name=%s err=%r", tt.get_name(), exc)

            t.add_done_callback(_done)
            return t

        async def stop_bg_tasks(self):
            if not self._bg_tasks:
                return
            for t in list(self._bg_tasks):
                t.cancel()
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            for t in list(self._bg_tasks):
                if t.cancelled():
                    log.info("E_DISCORD_TASK_CANCELLED name=%s", t.get_name())
            self._bg_tasks.clear()
    # [ANCHOR:DISCORD_TASKS] end

    class FTMDiscordBot(TaskRegistryMixin, commands.Bot):
        def __init__(self, bus: StateBus) -> None:
            intents = discord.Intents.default()
            super().__init__(command_prefix="!", intents=intents)
            self.bus = bus
            self.panel: Optional[PanelManager] = None
            self.analysis_pub: Optional[AnalysisPublisher] = None
            self._dash_task_started = False
            self._dash_channel = int(os.getenv("DISCORD_CHANNEL_ID_DASHBOARD", "0") or "0")


        # [ANCHOR:DISCORD_BOT]
        async def setup_hook(self) -> None:
            sync_fn = setup_panel_commands(self)
            await sync_fn()
            self.panel = PanelManager(self)
            async def _init_dashboard():
                def render_first():
                    return "📊 **FTM2 KPI 대시보드**\n(초기화 중…)"
                await ensure_dashboard_message(self, self._dash_channel, render_first)
            self.add_bg_task(_init_dashboard(), "dashboard_pin_recover")

            # [ANCHOR:DISCORD_TASKS] begin
            interval = int(os.getenv("ANALYSIS_REPORT_SEC", "60").strip())
            self.analysis_pub = AnalysisPublisher(self, self.bus, interval_s=interval)
            # [ANCHOR:DISCORD_TASKS] end


        async def on_ready(self) -> None:
            log.info("[DISCORD][READY] 로그인: %s (%s)", self.user, self.user and self.user.id)
            await self.panel.ensure_panel_message()

            if not getattr(self, "_dash_task_started", False):
                self._dash_task_started = True
                self._update_dashboard.start()
            # [ANCHOR:DISCORD_TASKS] begin
            t = self.analysis_pub.start()
            if t:
                self.add_bg_task(t, "analysis")

            # [ANCHOR:DISCORD_TASKS] end

        async def on_app_command_error(self, ia, error: Exception):
            from discord.app_commands.errors import CommandNotFound
            if isinstance(error, CommandNotFound):
                await ia.response.send_message(
                    "명령을 찾을 수 없습니다. 입력은 `/panel` 입니다. (표시는 **패널**)",
                    ephemeral=True,
                )
                return
            log.warning("[DISCORD][CMD_ERROR] %s", error)

        async def close(self) -> None:
            try:
                if hasattr(self, "_update_dashboard"):
                    self._update_dashboard.cancel()
            except Exception:
                pass
            # [ANCHOR:DISCORD_TASKS] begin
            await self.stop_bg_tasks()

            try:
                if hasattr(self, "analysis_pub"):
                    self.analysis_pub.stop()
            except Exception:
                pass
            try:
                if hasattr(self, "panel"):
                    await self.panel.close()
            except Exception:
                pass
            # [ANCHOR:DISCORD_TASKS] end
            try:
                await super().close()
            finally:
                log.info("[DISCORD] closed")


        @tasks.loop(seconds=15)
        async def _update_dashboard(self):  # pragma: no cover - 실제 실행 환경 의존
            try:
                snap = self.bus.snapshot()
                txt = render_dashboard(snap)
                await update_dashboard(self, self._dash_channel, txt)
            except Exception as e:
                log.warning("[대시보드] 업데이트 실패: %s", e)

        @_update_dashboard.before_loop  # pragma: no cover - 실제 실행 환경 의존
        async def _before_update(self):
            await self.wait_until_ready()



    def run_discord_bot(bus: StateBus) -> None:
        token = os.getenv("DISCORD_BOT_TOKEN") or ""
        if not token:
            log.warning("🔒 DISCORD_BOT_TOKEN 이 비어 있습니다. Discord 기능은 비활성화됩니다.")
            return
        bot = FTMDiscordBot(bus)

        async def _main():
            async with bot:
                await bot.start(token)

        try:
            asyncio.run(_main())
        except KeyboardInterrupt:  # pragma: no cover
            log.info("[DISCORD] 종료 요청")
