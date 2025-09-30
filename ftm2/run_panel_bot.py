from __future__ import annotations

import logging
import os

from ftm2.exchange.binance import BinanceClient
from ftm2.notify.discord import Alerts
from ftm2.panel.discord_controls import ConfigStore, PanelApp


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN 필수")

    store = ConfigStore("ftm2.sqlite3")
    alerts = Alerts()
    bx = BinanceClient(mode=os.getenv("MODE", "testnet"))

    app = PanelApp(store, alerts, bx)
    app.run(token)


if __name__ == "__main__":
    main()
