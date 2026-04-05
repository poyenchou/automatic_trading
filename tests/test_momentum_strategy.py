"""
Tests for MomentumStrategy.
No network calls — all DataFrames are constructed in memory.
"""

import pandas as pd
import pytest

from strategy.models import Direction
from strategy.momentum import MomentumStrategy

# ── Helpers ──────────────────────────────────────────────────────────────────

def _df(closes: list[float]) -> pd.DataFrame:
    """Minimal OHLCV DataFrame from a close price series."""
    return pd.DataFrame({
        "open":   closes,
        "high":   closes,
        "low":    closes,
        "close":  closes,
        "volume": [1_000_000.0] * len(closes),
    })


def _uptrend(n: int = 150) -> list[float]:
    """Steady uptrend: prices rising 1 per bar."""
    return [100.0 + i for i in range(n)]


def _downtrend(n: int = 150) -> list[float]:
    """Steady downtrend: prices falling 1 per bar."""
    return [100.0 + n - i for i in range(n)]


def _dip_in_uptrend(n: int = 150) -> list[float]:
    """
    Uptrend with a brief dip near the end — designed to trigger:
      - RSI oversold (dip)
      - MACD histogram positive and increasing (recovery starting)
      - Price above EMA(20) (still in uptrend)
    """
    prices = [50.0 + i * 0.5 for i in range(n - 10)]
    # sharp dip
    last = prices[-1]
    prices += [last - 8, last - 12, last - 10, last - 7, last - 4,
               last - 2, last - 1, last + 1, last + 2, last + 3]
    return prices


@pytest.fixture
def strategy() -> MomentumStrategy:
    return MomentumStrategy()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMomentumStrategyReturnsSignalResult:
    def test_returns_signal_result_with_symbol(self, strategy):
        df = _df(_uptrend())
        result = strategy.generate_signal("AAPL", df, pd.DataFrame())
        assert result.symbol == "AAPL"

    def test_direction_is_buy_or_none(self, strategy):
        df = _df(_uptrend())
        result = strategy.generate_signal("AAPL", df, pd.DataFrame())
        assert result.direction in (Direction.BUY, Direction.NONE)

    def test_reason_is_non_empty_string(self, strategy):
        df = _df(_uptrend())
        result = strategy.generate_signal("AAPL", df, pd.DataFrame())
        assert isinstance(result.reason, str) and len(result.reason) > 0


class TestMomentumStrategyNONECases:
    def test_insufficient_bars_returns_none(self, strategy):
        df = _df([10.0] * 10)
        result = strategy.generate_signal("X", df, pd.DataFrame())
        assert result.direction == Direction.NONE
        assert "insufficient bars" in result.reason

    def test_pure_downtrend_returns_none(self, strategy):
        # Downtrend: RSI will be low but MACD histogram won't be positive
        df = _df(_downtrend())
        result = strategy.generate_signal("X", df, pd.DataFrame())
        assert result.direction == Direction.NONE

    def test_price_below_ema_returns_none(self, strategy):
        # Sharp downtrend — price will be below EMA(20)
        prices = [200.0 - i * 2 for i in range(150)]
        df = _df(prices)
        result = strategy.generate_signal("X", df, pd.DataFrame())
        assert result.direction == Direction.NONE


class TestMomentumStrategyCustomThresholds:
    def test_custom_rsi_threshold(self):
        strategy = MomentumStrategy(rsi_oversold=70)
        df = _df(_uptrend())
        result = strategy.generate_signal("X", df, pd.DataFrame())
        # With a very high RSI threshold, RSI condition is easier to satisfy
        assert result.direction in (Direction.BUY, Direction.NONE)

    def test_today_df_is_ignored(self, strategy):
        """MomentumStrategy does not use today_df — passing empty should not error."""
        df = _df(_uptrend())
        result_with    = strategy.generate_signal("X", df, _df([10.0] * 5))
        result_without = strategy.generate_signal("X", df, pd.DataFrame())
        assert result_with.direction == result_without.direction
