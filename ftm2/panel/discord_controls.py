from __future__ import annotations

from typing import Callable, Dict
import logging
import os

try:
    import discord  # discord.py 2.x
    from discord import app_commands
except Exception:  # pragma: no cover - optional dependency
    discord = None  # type: ignore

from ftm2.exchange.binance import BinanceClient
from ftm2.db.core import config_get, config_set, get_conn
from ftm2.notify.discord import Alerts
from ftm2.ops.hotreload import HotReloader
from ftm2.risk.profile import RiskProfileApplier

log = logging.getLogger("ftm2.panel")


class ConfigStore:
    def __init__(self, db_path: str = "ftm2.sqlite3") -> None:
        self.db = db_path
        get_conn(db_path)

    def set(self, k: str, v: str) -> None:
        config_set(k, str(v))

    def get(self, k: str, default=None):
        return config_get(k, default)

    def dump(self) -> Dict:
        conn = get_conn(self.db)
        cur = conn.execute("SELECT key, val FROM config")
        rows = cur.fetchall()
        return {row[0]: row[1] for row in rows}


# [ANCHOR:PANEL_TUNER]
class PanelTuner:
    def __init__(self, store: ConfigStore, on_change: Callable[[str, str], None]):
        self.store = store
        self.on_change = on_change

    def _map_key(self, key: str) -> str:
        return {
            "tenkan": "IK_TENKAN",
            "kijun": "IK_KIJUN",
            "sen": "IK_SEN",
            "twist_guard": "IK_TWIST_GUARD",
            "thick_pct": "IK_THICK_PCT",
            "w_imk": "W_IMK",
            "w_trend": "SC_W_TREND",
            "w_mr": "SC_W_MR",
            "w_brk": "SC_W_BRK",
            "gates": "IK_GATES",
            "align": "REGIME_ALIGN_MODE",
            "strategy": "EXEC_STRATEGY",
        }.get(key, key)

    def _apply_set(self, key: str, value):
        env_key = self._map_key(key)
        if not env_key:
            return {"ok": False, "error": "unknown_key"}
        self.store.set(f"env.{env_key}", str(value))
        if callable(self.on_change):
            self.on_change(env_key, str(value))
        return {"ok": True, "key": key, "env": env_key, "value": value}

    def apply_command(self, cmd: str, args: Dict):
        if cmd in {"ik.set", "weights.set", "gates.set", "strategy.set"}:
            return self._apply_set(args.get("key"), args.get("value"))
        if cmd in {"ik.nudge", "+", "-"}:
            key = args.get("key")
            delta = float(args.get("delta", 0))
            env_key = self._map_key(key)
            current = float(self.store.get(f"env.{env_key}", os.getenv(env_key, "0")))
            return self._apply_set(key, current + delta)
        return {"ok": False, "error": "unknown_cmd"}


class _KV:
    def __init__(self, store):
        self.store = store

    def get(self, k, d=None):
        return self.store.get(k, d)

    def set(self, k, v):
        self.store.set(k, v)


class PanelApp:
    """Discord 봇 등록기. discord.py가 설치되어 있지 않으면 skip."""

    # [ANCHOR:PANEL_HANDLERS]
    def __init__(self, store, alerts: Alerts, bx: BinanceClient):
        self.alerts = alerts
        self.store = _KV(store)
        self.bx = bx
        self.hot = HotReloader(on_announce=lambda msg: self.alerts.config_announce(msg)).apply
        self.prof = RiskProfileApplier(store, self.hot)
        self.bot = None
        if discord:
            intents = discord.Intents.default()
            self.bot = discord.Client(intents=intents)
            self.tree = app_commands.CommandTree(self.bot)
            self._register_commands()
            self.bot.event(self.on_ready)

    async def on_ready(self):
        try:
            await self.tree.sync()
            log.info("Discord commands synced")
        except Exception as exc:  # pragma: no cover - logging only
            log.warning("Discord sync fail %s", exc)

    def _register_commands(self):
        if not discord:
            return

        @self.tree.command(name="risk", description="공격성(1~10) 설정")
        @app_commands.describe(level="1~10")
        async def risk_cmd(inter: discord.Interaction, level: int):
            out = self.prof.apply_level(level)
            msg = self._format_profile_msg(level, out)
            self.alerts.config_profile(level, out)
            await inter.response.send_message(msg, ephemeral=True)

        @self.tree.command(name="lev", description="심볼별 레버리지 설정")
        @app_commands.describe(symbol="예: BTCUSDT", value="1~125")
        async def lev_cmd(inter: discord.Interaction, symbol: str, value: int):
            sym = symbol.upper()
            try:
                self.bx.set_leverage(sym, int(value))
                self.store.set(f"lev.{sym}", str(int(value)))
                self.alerts.config_leverage(sym, int(value), ok=True)
                await inter.response.send_message(
                    f"Leverage {sym} → {int(value)}x (OK)", ephemeral=True
                )
            except Exception as exc:
                self.alerts.config_leverage(sym, int(value), ok=False, err=str(exc))
                await inter.response.send_message(
                    f"❌ leverage set fail: {exc}", ephemeral=True
                )

        @self.tree.command(name="slip", description="심볼별 슬리피지 허용 bps 설정")
        @app_commands.describe(symbol="예: BTCUSDT", value="bps (예 7)")
        async def slip_cmd(inter: discord.Interaction, symbol: str, value: float):
            sym = symbol.upper()
            try:
                bps = float(value)
            except (TypeError, ValueError):
                await inter.response.send_message("❌ invalid bps value", ephemeral=True)
                return
            config_set(f"slippage.bps.{sym}", str(bps))
            self.alerts.config_announce(f"slippage.bps.{sym} → {bps}")
            await inter.response.send_message(
                f"slippage {sym} = {bps} bps", ephemeral=True
            )

        @self.tree.command(name="panel", description="리스크/레버리지 버튼 패널 표시")
        @app_commands.describe(symbol="레버리지 조절할 심볼")
        async def panel_cmd(inter: discord.Interaction, symbol: str):
            await inter.response.send_message(
                content=self._panel_text(symbol),
                view=self._make_view(symbol),
                ephemeral=True,
            )

    def _make_view(self, symbol: str):
        if not discord:
            return None

        sym = symbol.upper()

        class PanelView(discord.ui.View):
            def __init__(self, outer: PanelApp, sym: str):
                super().__init__(timeout=120)
                self.outer = outer
                self.sym = sym

            @discord.ui.button(label="Risk −", style=discord.ButtonStyle.secondary, custom_id="risk:-")
            async def risk_minus(self, inter: discord.Interaction, _btn: discord.ui.Button):
                cur = int(self.outer.store.get("profile.level", "5") or "5")
                new = max(1, cur - 1)
                out = self.outer.prof.apply_level(new)
                self.outer.alerts.config_profile(new, out)
                await inter.response.edit_message(
                    content=self.outer._panel_text(self.sym), view=self
                )

            @discord.ui.button(label="Risk +", style=discord.ButtonStyle.primary, custom_id="risk:+")
            async def risk_plus(self, inter: discord.Interaction, _btn: discord.ui.Button):
                cur = int(self.outer.store.get("profile.level", "5") or "5")
                new = min(10, cur + 1)
                out = self.outer.prof.apply_level(new)
                self.outer.alerts.config_profile(new, out)
                await inter.response.edit_message(
                    content=self.outer._panel_text(self.sym), view=self
                )

            @discord.ui.button(label="Lev −", style=discord.ButtonStyle.secondary, custom_id="lev:-")
            async def lev_minus(self, inter: discord.Interaction, _btn: discord.ui.Button):
                cur = int(self.outer.store.get(f"lev.{self.sym}", "5") or "5")
                new = max(1, cur - 1)
                try:
                    self.outer.bx.set_leverage(self.sym, new)
                    self.outer.store.set(f"lev.{self.sym}", str(new))
                    self.outer.alerts.config_leverage(self.sym, new, ok=True)
                except Exception as exc:
                    self.outer.alerts.config_leverage(
                        self.sym, new, ok=False, err=str(exc)
                    )
                await inter.response.edit_message(
                    content=self.outer._panel_text(self.sym), view=self
                )

            @discord.ui.button(label="Lev +", style=discord.ButtonStyle.primary, custom_id="lev:+")
            async def lev_plus(self, inter: discord.Interaction, _btn: discord.ui.Button):
                cur = int(self.outer.store.get(f"lev.{self.sym}", "5") or "5")
                new = min(125, cur + 1)
                try:
                    self.outer.bx.set_leverage(self.sym, new)
                    self.outer.store.set(f"lev.{self.sym}", str(new))
                    self.outer.alerts.config_leverage(self.sym, new, ok=True)
                except Exception as exc:
                    self.outer.alerts.config_leverage(
                        self.sym, new, ok=False, err=str(exc)
                    )
                await inter.response.edit_message(
                    content=self.outer._panel_text(self.sym), view=self
                )

        return PanelView(self, sym)

    def _panel_text(self, symbol: str) -> str:
        sym = symbol.upper()
        lvl = int(self.store.get("profile.level", "5") or "5")
        lev = int(self.store.get(f"lev.{sym}", "5") or "5")
        bps_val = config_get(
            f"slippage.bps.{sym}",
            config_get("slippage.bps.*", os.getenv("EXEC_SLIPPAGE_BPS", "6")),
        )
        bps = bps_val if bps_val is not None else os.getenv("EXEC_SLIPPAGE_BPS", "6")
        return (
            "**Risk/Leverage Panel**\n"
            f"• Symbol: `{sym}`\n"
            f"• Risk Level: `{lvl}` (1=보수 ←→ 10=공격)\n"
            f"• Leverage: `{lev}x`\n"
            f"• Slippage: `{bps} bps`\n"
            "버튼으로 값 변경 시 DB 저장→핫리로드→Alerts 공지"
        )

    def _format_profile_msg(self, level: int, envs: Dict[str, str]) -> str:
        keys = [
            "RISK_TARGET_PCT",
            "EXEC_SLIPPAGE_BPS",
            "REENTER_COOLDOWN_S",
            "IK_TWIST_GUARD",
            "IK_THICK_PCT",
            "W_IMK",
            "SC_W_TREND",
            "SC_W_MR",
            "CORR_CAP_PER_SIDE",
            "DAILY_MAX_LOSS_PCT",
            "REGIME_ALIGN_MODE",
        ]
        parts = [f"{k}={envs.get(k, '-')}" for k in keys]
        return f"Risk profile={level} 적용\n" + " • " + "\n • ".join(parts)

    def run(self, token: str) -> None:
        if not discord:
            log.warning("discord.py 미설치 — 패널 비활성")
            return
        self.bot.run(token)
