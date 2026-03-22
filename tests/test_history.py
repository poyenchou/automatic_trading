"""Unit tests for HistoricalDataFetcher (market_data/history.py)."""

import pytest
import pandas as pd
from datetime import timezone
from unittest.mock import MagicMock

from broker.client import AlpacaClient
from broker.exceptions import RateLimitError
from broker.models import OHLCVBar
from market_data.history import HistoricalDataFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(ts: str, o=100.0, h=101.0, l=99.0, c=100.5, v=1_000_000.0) -> OHLCVBar:
    return OHLCVBar(t=ts, o=o, h=h, l=l, c=c, v=v)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock(spec=AlpacaClient)


@pytest.fixture
def fetcher(mock_client) -> HistoricalDataFetcher:
    return HistoricalDataFetcher(client=mock_client)


@pytest.fixture
def three_bars() -> list[OHLCVBar]:
    return [
        _make_bar("2024-01-15T14:30:00Z", o=100.0, c=100.5),
        _make_bar("2024-01-15T14:35:00Z", o=100.5, c=101.0),
        _make_bar("2024-01-15T14:40:00Z", o=101.0, c=101.8),
    ]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_fetch_bars_returns_dataframe(fetcher, mock_client, three_bars):
    mock_client.get_historical_bars.return_value = three_bars
    df = fetcher.fetch_bars("AAPL")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3


def test_fetch_bars_schema(fetcher, mock_client, three_bars):
    mock_client.get_historical_bars.return_value = three_bars
    df = fetcher.fetch_bars("AAPL")

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "timestamp"
    assert set(df.columns) == {"open", "high", "low", "close", "volume"}
    for col in df.columns:
        assert df[col].dtype == "float64", f"{col} is not float64"


def test_fetch_bars_index_is_utc_aware(fetcher, mock_client, three_bars):
    mock_client.get_historical_bars.return_value = three_bars
    df = fetcher.fetch_bars("AAPL")
    assert df.index.tz == timezone.utc


def test_fetch_bars_ascending_order(fetcher, mock_client):
    # Pass bars in descending order — output should still be ascending
    bars = [
        _make_bar("2024-01-15T14:40:00Z"),
        _make_bar("2024-01-15T14:35:00Z"),
        _make_bar("2024-01-15T14:30:00Z"),
    ]
    mock_client.get_historical_bars.return_value = bars
    df = fetcher.fetch_bars("AAPL")
    assert df.index.is_monotonic_increasing


def test_fetch_bars_passes_args_to_client(fetcher, mock_client, three_bars):
    mock_client.get_historical_bars.return_value = three_bars
    fetcher.fetch_bars(
        "TSLA",
        timeframe="1Min",
        start="2024-01-15T09:30:00Z",
        end="2024-01-15T16:00:00Z",
        limit=100,
    )
    mock_client.get_historical_bars.assert_called_once_with(
        symbol="TSLA",
        timeframe="1Min",
        start="2024-01-15T09:30:00Z",
        end="2024-01-15T16:00:00Z",
        limit=100,
    )


# ---------------------------------------------------------------------------
# Empty response
# ---------------------------------------------------------------------------


def test_fetch_bars_empty_response(fetcher, mock_client):
    mock_client.get_historical_bars.return_value = []
    df = fetcher.fetch_bars("AAPL")

    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert isinstance(df.index, pd.DatetimeIndex)
    assert set(df.columns) == {"open", "high", "low", "close", "volume"}


# ---------------------------------------------------------------------------
# NaN handling
# ---------------------------------------------------------------------------


def test_fetch_bars_drops_nan_rows(fetcher, mock_client):
    bars = [
        _make_bar("2024-01-15T14:30:00Z"),
        _make_bar("2024-01-15T14:35:00Z", v=float("nan")),  # bad bar
        _make_bar("2024-01-15T14:40:00Z"),
    ]
    mock_client.get_historical_bars.return_value = bars
    df = fetcher.fetch_bars("AAPL")
    assert len(df) == 2
    assert not df.isnull().any().any()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_fetch_bars_raises_on_empty_symbol(fetcher):
    with pytest.raises(ValueError, match="symbol"):
        fetcher.fetch_bars("")


def test_fetch_bars_raises_on_whitespace_symbol(fetcher):
    with pytest.raises(ValueError, match="symbol"):
        fetcher.fetch_bars("   ")


@pytest.mark.parametrize("limit", [0, -1, -100])
def test_fetch_bars_raises_on_nonpositive_limit(fetcher, limit):
    with pytest.raises(ValueError, match="limit"):
        fetcher.fetch_bars("AAPL", limit=limit)


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


def test_fetch_bars_propagates_broker_error(fetcher, mock_client):
    mock_client.get_historical_bars.side_effect = RateLimitError("rate limit")
    with pytest.raises(RateLimitError):
        fetcher.fetch_bars("AAPL")
