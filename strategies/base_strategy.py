"""Abstract base class for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from binance.client import Client


class BaseStrategy(ABC):
    """All strategy implementations should inherit from this class."""

    def __init__(self, strategy_name: str, symbol: str, settings: Dict[str, Any]) -> None:
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.settings = settings
        print(f"전략 '{self.strategy_name}'이(가) {self.symbol}에 대해 초기화되었습니다.")

    @abstractmethod
    def check_signal(self, client: Client) -> Optional[Dict[str, Any]]:
        """Return order parameters when a trading signal is present."""

        raise NotImplementedError
