from abc import ABC, abstractmethod

import pandas as pd

from strategy.models import SignalResult


class Strategy(ABC):
    @abstractmethod
    def generate_signal(self, symbol: str, df: pd.DataFrame, today_df: pd.DataFrame) -> SignalResult:
        """
        Compute a trading signal for the given symbol.

        Args:
            symbol:   Stock ticker, e.g. "AAPL".
            df:       Full history DataFrame (30 days of OHLCV bars, UTC index).
                      Used for indicators that need a long warm-up (RSI, MACD,
                      relative_volume lookback).
            today_df: Current session bars only (OHLCV, UTC index).
                      Used for session-anchored indicators (VWAP, first_dip_signal).

        Returns:
            SignalResult with direction BUY or NONE and a human-readable reason.
        """
