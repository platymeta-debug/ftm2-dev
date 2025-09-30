"""Example RSI-based trading strategy implementation."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import pandas_ta as ta
from binance.client import Client

from .base_strategy import BaseStrategy


class SimpleRSIStrategy(BaseStrategy):
    """RSI-based mean reversion strategy generating market orders."""

    def __init__(self, symbol: str, settings: Dict[str, Any]):
        super().__init__(strategy_name="SimpleRSI", symbol=symbol, settings=settings)
        self.timeframe = settings.get("timeframe", "5m")
        self.rsi_period = settings.get("rsi_period", 14)
        self.rsi_oversold = settings.get("rsi_oversold", 30)
        self.rsi_overbought = settings.get("rsi_overbought", 70)
        self.trade_quantity = settings.get("quantity", 0.001)

    def _calculate_rsi(self, client: Client) -> Optional[float]:
        """Calculate the most recent RSI value from futures candlestick data."""

        try:
            klines = client.futures_klines(
                symbol=self.symbol,
                interval=self.timeframe,
                limit=self.rsi_period + 10,
            )
            df = pd.DataFrame(
                klines,
                columns=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "close_time",
                    "quote_asset_volume",
                    "number_of_trades",
                    "taker_buy_base_asset_volume",
                    "taker_buy_quote_asset_volume",
                    "ignore",
                ],
            )
            df["close"] = pd.to_numeric(df["close"])
            df["rsi"] = ta.rsi(df["close"], length=self.rsi_period)

            last_rsi = float(df["rsi"].iloc[-1])
            print(f"[{self.strategy_name}] 현재 {self.symbol} ({self.timeframe}) RSI: {last_rsi:.2f}")
            return last_rsi
        except Exception as exc:  # pragma: no cover - external dependency
            print(f"[{self.strategy_name}] RSI 계산 중 오류 발생: {exc}")
            return None

    def check_signal(self, client: Client) -> Optional[Dict[str, Any]]:
        rsi = self._calculate_rsi(client)
        if rsi is None:
            return None

        if rsi < self.rsi_oversold:
            print(f"🚀 [{self.strategy_name}] 매수 신호 발생! RSI: {rsi:.2f}")
            return {
                "symbol": self.symbol,
                "side": "BUY",
                "type": "MARKET",
                "quantity": self.trade_quantity,
            }

        if rsi > self.rsi_overbought:
            print(f"📉 [{self.strategy_name}] 매도 신호 발생! RSI: {rsi:.2f}")
            return {
                "symbol": self.symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": self.trade_quantity,
            }

        return None
