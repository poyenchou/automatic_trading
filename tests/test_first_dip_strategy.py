"""
Tests for FirstDipStrategy.
No network calls — float_fetcher is mocked, DataFrames are constructed in memory.
"""

from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from strategy.first_dip import FirstDipStrategy
from strategy.models import Direction

ET = ZoneInfo("America/New_York")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ts(time_str: str, date: str = "2026-04-04") -> pd.Timestamp:
    return pd.Timestamp(f"{date} {time_str}", tz=ET).tz_convert("UTC")


def _bar(ts: pd.Timestamp, open_: float, high: float, low: float,
         close: float, volume: float) -> dict:
    return {"timestamp": ts, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


def _make_df(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index, tz="UTC")
    return df.astype({"open": float, "high": float, "low": float,
                      "close": float, "volume": float})


def _mock_float_fetcher(is_low: bool) -> MagicMock:
    ff = MagicMock()
    ff.is_low_float.return_value = is_low
    return ff


def _prior_bars(prev_close: float = 10.0, n: int = 25) -> list[dict]:
    """25 bars from the prior session (yesterday) for relative_volume lookback."""
    return [
        _bar(_ts("09:30", "2026-04-03") + pd.Timedelta(minutes=5 * i),
             prev_close, prev_close, prev_close, prev_close, 1_000_000.0)
        for i in range(n)
    ]


def _today_bars(
    open_price: float = 12.0,
    surge: bool = True,
    dip: bool = True,
    volume_mult: float = 3.0,
    time_str: str = "09:40",
) -> list[dict]:
    """
    Construct today's session bars.
    Bar 0 (9:30): opens at open_price, surges if surge=True
    Bar 1 (9:35): continuation
    Bar 2 (9:40): dip bar — low touches support, close reclaims if dip=True
    """
    base_vol = 1_000_000.0 * volume_mult
    bars = [
        _bar(_ts("09:30"), open_price, open_price * 1.05, open_price, open_price * 1.04, base_vol),
    ]
    if surge:
        bars.append(_bar(_ts("09:35"), open_price * 1.04, open_price * 1.08,
                         open_price * 1.03, open_price * 1.07, base_vol))
    if dip:
        # low dips to near open (below VWAP/EMA), closes back above
        bars.append(_bar(_ts(time_str), open_price * 1.07, open_price * 1.08,
                         open_price * 0.99, open_price * 1.05, base_vol))
    return bars


@pytest.fixture
def strategy_no_float() -> FirstDipStrategy:
    """Strategy with float filter disabled."""
    return FirstDipStrategy(float_fetcher=None, min_rel_vol=2.0, min_gap_pct=0.10)


@pytest.fixture
def full_df() -> pd.DataFrame:
    return _make_df(_prior_bars(prev_close=10.0))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestFirstDipStrategyNONECases:
    def test_empty_today_df_returns_none(self, strategy_no_float, full_df):
        result = strategy_no_float.generate_signal("X", full_df, pd.DataFrame())
        assert result.direction == Direction.NONE
        assert "no session bars" in result.reason

    def test_no_prior_session_still_evaluates(self, strategy_no_float):
        # With no prior bars, gap_pct defaults to 0.0 — strategy continues to other gates.
        # Gap pre-filtering is now the screener's responsibility, not the strategy's.
        today_df = _make_df(_today_bars())
        result = strategy_no_float.generate_signal("X", today_df, today_df)
        assert result.direction == Direction.NONE  # will fail on vol/window/signal, not gap

    def test_low_float_filter_fails_returns_none(self, full_df):
        strategy = FirstDipStrategy(
            float_fetcher=_mock_float_fetcher(is_low=False),
            min_gap_pct=0.10,
            min_rel_vol=2.0,
        )
        today_bars = _today_bars(open_price=11.5)
        today_df = _make_df(today_bars)
        df = _make_df(_prior_bars(prev_close=10.0) + today_bars)
        result = strategy.generate_signal("X", df, today_df)
        assert result.direction == Direction.NONE
        assert "float" in result.reason

    def test_low_relative_volume_returns_none(self, full_df):
        strategy = FirstDipStrategy(float_fetcher=None, min_gap_pct=0.10, min_rel_vol=2.0)
        # volume_mult=0.5 → rel_vol < 1x, well below 2x
        today_bars = _today_bars(open_price=11.5, volume_mult=0.5)
        today_df = _make_df(today_bars)
        df = _make_df(_prior_bars(prev_close=10.0) + today_bars)
        result = strategy.generate_signal("X", df, today_df)
        assert result.direction == Direction.NONE
        assert "relative volume" in result.reason

    def test_outside_prime_window_returns_none(self):
        # Use min_rel_vol=0 so relative volume gate passes, isolating the time check
        strategy = FirstDipStrategy(float_fetcher=None, min_gap_pct=0.10, min_rel_vol=0.0)
        today_bars = _today_bars(open_price=11.5, time_str="11:00")
        today_df = _make_df(today_bars)
        df = _make_df(_prior_bars(prev_close=10.0) + today_bars)
        result = strategy.generate_signal("X", df, today_df)
        assert result.direction == Direction.NONE
        assert "prime window" in result.reason


class TestFirstDipStrategyReturnsSignalResult:
    def test_symbol_preserved_in_result(self, strategy_no_float, full_df):
        today_df = _make_df(_today_bars())
        result = strategy_no_float.generate_signal("AAPL", full_df, today_df)
        assert result.symbol == "AAPL"

    def test_direction_is_buy_or_none(self, strategy_no_float, full_df):
        today_df = _make_df(_today_bars())
        result = strategy_no_float.generate_signal("X", full_df, today_df)
        assert result.direction in (Direction.BUY, Direction.NONE)

    def test_reason_is_non_empty_string(self, strategy_no_float, full_df):
        today_df = _make_df(_today_bars())
        result = strategy_no_float.generate_signal("X", full_df, today_df)
        assert isinstance(result.reason, str) and len(result.reason) > 0

    def test_float_fetcher_none_skips_float_check(self):
        """Passing float_fetcher=None should not raise and should proceed past gate 2."""
        strategy = FirstDipStrategy(float_fetcher=None, min_gap_pct=0.10, min_rel_vol=2.0)
        today_bars = _today_bars(open_price=11.5)
        today_df = _make_df(today_bars)
        df = _make_df(_prior_bars(prev_close=10.0) + today_bars)
        result = strategy.generate_signal("X", df, today_df)
        # Should not fail due to missing float fetcher
        assert result.symbol == "X"

    def test_float_fetcher_is_called_with_symbol(self):
        ff = _mock_float_fetcher(is_low=True)
        strategy = FirstDipStrategy(float_fetcher=ff, min_gap_pct=0.10, min_rel_vol=0.0)
        today_bars = _today_bars(open_price=11.5)
        today_df = _make_df(today_bars)
        df = _make_df(_prior_bars(prev_close=10.0) + today_bars)
        strategy.generate_signal("MEME", df, today_df)
        ff.is_low_float.assert_called_once_with("MEME", max_float=20_000_000)
