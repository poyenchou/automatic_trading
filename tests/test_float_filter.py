"""
Tests for market_data/float_filter.py.
yfinance network calls are mocked so tests run offline.
"""

from unittest.mock import MagicMock, patch

import pytest

from market_data.float_filter import DEFAULT_MAX_FLOAT, FloatFetcher


@pytest.fixture
def fetcher() -> FloatFetcher:
    return FloatFetcher()


def _mock_ticker(float_shares: int | None):
    """Return a mock yfinance Ticker whose .info has the given floatShares."""
    ticker = MagicMock()
    ticker.info = {"floatShares": float_shares} if float_shares is not None else {}
    return ticker


class TestGetFloatShares:
    def test_returns_float_shares(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(5_000_000)):
            assert fetcher.get_float_shares("XYZ") == 5_000_000

    def test_returns_none_when_missing(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(None)):
            assert fetcher.get_float_shares("XYZ") is None

    def test_returns_none_on_exception(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", side_effect=Exception("network error")):
            assert fetcher.get_float_shares("XYZ") is None


class TestIsLowFloat:
    def test_below_threshold_returns_true(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(10_000_000)):
            assert fetcher.is_low_float("XYZ") is True

    def test_at_threshold_returns_true(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(DEFAULT_MAX_FLOAT)):
            assert fetcher.is_low_float("XYZ") is True

    def test_above_threshold_returns_false(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(50_000_000)):
            assert fetcher.is_low_float("XYZ") is False

    def test_no_data_returns_true(self, fetcher):
        # Unknown float → allow through (likely small-cap, too new for yfinance)
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(None)):
            assert fetcher.is_low_float("XYZ") is True

    def test_custom_threshold(self, fetcher):
        with patch("market_data.float_filter.yf.Ticker", return_value=_mock_ticker(8_000_000)):
            assert fetcher.is_low_float("XYZ", max_float=10_000_000) is True
            assert fetcher.is_low_float("XYZ", max_float=5_000_000) is False
