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
        from ftm2.discord_bot.dashboards import DashboardManager
        from ftm2.discord_bot.panel import setup_panel_commands
    except Exception:  # pragma: no cover
        from discord_bot.dashboards import DashboardManager  # type: ignore
        from discord_bot.panel import setup_panel_commands  # type: ignore

    class FTMDiscordBot(commands.Bot):
        def __init__(self, bus: StateBus) -> None:
            intents = discord.Intents.default()
            super().__init__(command_prefix="!", intents=intents)
            self.bus = bus
            self.dashboard: Optional[DashboardManager] = None
            self._dash_task_started = False

        async def setup_hook(self) -> None:
            # 대시보드 매니저 설치
            self.dashboard = DashboardManager(self)
            # 패널/명령 등록
            setup_panel_commands(self)

            # 글로벌 커맨드 동기화
            try:
                await self.tree.sync()
                log.info("[DISCORD] 슬래시 명령 동기화 완료")
            except Exception as e:  # pragma: no cover
                log.warning("[DISCORD] 슬래시 동기화 실패: %s", e)

        async def on_ready(self) -> None:  # pragma: no cover - 실제 실행 환경 의존
            log.info("[DISCORD][READY] 로그인: %s (%s)", self.user, self.user and self.user.id)
            # 대시보드 초기 메시지 확보
            try:
                await self.dashboard.ensure_dashboard_message()
            except Exception as e:
                log.warning("[대시보드] 초기화 실패: %s", e)

            # 대시보드 업데이트 루프 시작
            if not self._dash_task_started:
                self._dash_task_started = True
                self._update_dashboard.start()

        @tasks.loop(seconds=15)
        async def _update_dashboard(self):  # pragma: no cover - 실제 실행 환경 의존
            try:
                snap = self.bus.snapshot()
                await self.dashboard.update(snap)
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
