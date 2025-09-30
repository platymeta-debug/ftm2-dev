from __future__ import annotations

import os
import time
import requests

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHAN_DASH = int(os.getenv("DISCORD_CHANNEL_ID_DASHBOARD", "1412619011224506538"))
CHAN_ALERT = int(os.getenv("DISCORD_CHANNEL_ID_ALERTS", "1412619046326894652"))
CHAN_ANAL = int(os.getenv("DISCORD_CHANNEL_ID_ANALYSIS", "1412618994590158989"))
CHAN_PANEL = int(os.getenv("DISCORD_CHANNEL_ID_PANEL", "1412619029063143569"))

API_BASE = "https://discord.com/api/v10"


def _post_channel_message(channel_id: int, content: str) -> None:
    url = f"{API_BASE}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    data = {"content": content}
    resp = requests.post(url, headers=headers, json=data, timeout=10)
    if resp.status_code >= 300:
        raise RuntimeError(f"discord_post_fail {resp.status_code} {resp.text}")


# [ANCHOR:DISCORD_ALERTS]
class Alerts:
    def __init__(self, channel_id_alerts: int = CHAN_ALERT) -> None:
        self.chan = channel_id_alerts

    def ticket_issued(
        self,
        symbol: str,
        side: str,
        qty: float,
        notional: float,
        price: float,
        reason: str,
        link_id: str | None,
    ) -> None:
        dir_emoji = "🟢 BUY" if side.upper().startswith("B") else "🔴 SELL"
        msg = (
            f"**[TICKET] {dir_emoji} {symbol}**\n"
            f"• 수량: `{qty:.6f}`  (≈ ${notional:,.2f}) @ `{price:.2f}`\n"
            f"• 사유: {reason}\n"
            f"• link_id: `{link_id or '-'}`\n"
            f"• ts: <t:{int(time.time())}:T>"
        )
        _post_channel_message(self.chan, msg)

    # [ANCHOR:DISCORD_CONFIG_ALERTS]
    def config_profile(self, level: int, envs: dict) -> None:
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
        brief = ", ".join([f"{k}={envs.get(k)}" for k in keys if k in envs])
        _post_channel_message(
            self.chan,
            f"**[CONFIG] Risk profile={level} 적용**\n{brief}",
        )

    def config_leverage(
        self, symbol: str, value: int, ok: bool, err: str | None = None
    ) -> None:
        if ok:
            _post_channel_message(
                self.chan, f"**[CONFIG] Leverage {symbol} → {value}x (OK)**"
            )
        else:
            _post_channel_message(
                self.chan,
                f"**[CONFIG] Leverage {symbol} → {value}x (FAIL)**\n`{err}`",
            )

    def config_announce(self, line: str) -> None:
        _post_channel_message(self.chan, f"[CONFIG] {line}")
