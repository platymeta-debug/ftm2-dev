# -*- coding: utf-8 -*-
"""
Discord Bot Runner (스켈레톤)
- 한글 패널/대시보드/알림
- 대시보드 15초 주기 편집 업데이트
"""
from __future__ import annotations

import os
import json
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

try:  # runtime alerts queue
    from ftm2.discord_bot.notify import QUEUE as ALERTS_QUEUE
except Exception:  # pragma: no cover
    from discord_bot.notify import QUEUE as ALERTS_QUEUE  # type: ignore


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

        # [ANCHOR:DISCORD_BOT]
        async def setup_hook(self) -> None:
            # 대시보드 매니저 설치/초기 메시지 확보
            self.dashboard = DashboardManager(self)
            sync_fn = setup_panel_commands(self)
            await sync_fn()  # 길드 싱크 확정
            # 초기 대시보드 확보는 on_ready에서

        async def on_ready(self) -> None:

            log.info("[DISCORD][READY] 로그인: %s (%s)", self.user, self.user and self.user.id)
            try:
                await self.dashboard.ensure_dashboard_message()
            except Exception as e:
                log.warning("[대시보드] 초기화 실패: %s", e)
            # 주기 루프 시작(중복 스타트 방지)
            if not getattr(self, "_dash_task_started", False):
                self._dash_task_started = True
                self._update_dashboard.start()
                # 알림 펌프 루프가 있다면 여기도 start()
                self._pump_alerts.start()

        async def on_app_command_error(self, interaction: discord.Interaction, error: Exception):
            # 사용자가 /패널 입력 시 CommandNotFound → 안내
            try:
                from discord.app_commands.errors import CommandNotFound
                if isinstance(error, CommandNotFound):
                    await interaction.response.send_message(
                        "명령을 찾을 수 없습니다. 입력은 `/panel` 입니다. (표시는 **패널**)",
                        ephemeral=True,
                    )
                    return
            except Exception:
                pass
            log.warning("[DISCORD][CMD_ERROR] %s", error)

        async def close(self) -> None:
            # task 루프들 안전 종료
            try:
                if hasattr(self, "_update_dashboard"):
                    self._update_dashboard.cancel()
            except Exception:
                pass
            try:
                if hasattr(self, "_pump_alerts"):
                    self._pump_alerts.cancel()
            except Exception:
                pass
            try:
                await super().close()
            finally:
                log.info("[DISCORD] closed")


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


        @tasks.loop(seconds=2)
        async def _pump_alerts(self):  # pragma: no cover - 실제 실행 환경 의존
            """runtime/alerts_queue.jsonl 내용을 alerts 채널로 전송"""
            src = ALERTS_QUEUE
            tmp = ALERTS_QUEUE + ".sending"
            if not os.path.exists(src):
                return
            try:
                os.replace(src, tmp)
            except FileNotFoundError:
                return
            except Exception as e:
                log.warning("[ALERT_PUMP] rename fail: %s", e)
                return

            alert_id = int(os.getenv("CHAN_ALERTS_ID") or "0")
            ch = self.get_channel(alert_id) if alert_id else None
            if ch is None and alert_id:
                try:
                    ch = await self.fetch_channel(alert_id)
                except Exception as e:
                    log.warning("[ALERT_PUMP] fetch channel fail: %s", e)

            try:
                with open(tmp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        text = rec.get("text") or ""
                        if isinstance(ch, discord.TextChannel):
                            try:
                                await ch.send(text)
                            except Exception as e:
                                log.warning("[ALERT_PUMP] send fail: %s", e)
                        else:
                            log.info("[ALERT_PUMP][DRY] %s", text)
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass


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
