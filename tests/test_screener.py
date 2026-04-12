"""Unit tests for GapScreener (market_data/screener.py)."""

import pytest
from unittest.mock import MagicMock

from broker.client import AlpacaClient
from broker.exceptions import GatewayError
from market_data.models import ScreenerResult
from market_data.screener import GapScreener


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock(spec=AlpacaClient)


@pytest.fixture
def screener(mock_client, settings) -> GapScreener:
    return GapScreener(client=mock_client, settings=settings)


def _make_snap(prev_close: float, daily_open: float, latest_price: float, volume: float = 1_000_000) -> dict:
    return {
        "prevDailyBar": {"c": prev_close},
        "dailyBar":     {"o": daily_open, "c": daily_open, "v": volume},
        "latestTrade":  {"p": latest_price},
    }


def test_get_gappers_returns_qualifying_symbols(screener, mock_client):
    mock_client.get_assets.return_value = [
        {"symbol": "AIXI", "exchange": "NASDAQ", "tradable": True},
        {"symbol": "AAPL", "exchange": "NASDAQ", "tradable": True},
    ]
    # AIXI: 25% gap, price $5 — should pass
    # AAPL: 2% gap — should be filtered out
    mock_client.get_snapshots.return_value = {
        "AIXI": _make_snap(prev_close=4.0, daily_open=5.0, latest_price=5.0),
        "AAPL": _make_snap(prev_close=200.0, daily_open=204.0, latest_price=204.0),
    }
    results = screener.get_gappers()

    assert len(results) == 1
    assert results[0].symbol == "AIXI"
    assert abs(results[0].gap_pct - 0.25) < 0.001


def test_get_gappers_filters_price_below_minimum(screener, mock_client):
    mock_client.get_assets.return_value = [
        {"symbol": "RDGT", "exchange": "NYSE", "tradable": True},
    ]
    # 50% gap but price $0.03 — below min_stock_price
    mock_client.get_snapshots.return_value = {
        "RDGT": _make_snap(prev_close=0.02, daily_open=0.03, latest_price=0.03),
    }
    results = screener.get_gappers()
    assert results == []


def test_get_gappers_excludes_non_nasdaq_nyse(screener, mock_client):
    mock_client.get_assets.return_value = [
        {"symbol": "OTCFOO", "exchange": "OTC", "tradable": True},
        {"symbol": "GOOD",   "exchange": "NASDAQ", "tradable": True},
    ]
    mock_client.get_snapshots.return_value = {
        "GOOD": _make_snap(prev_close=4.0, daily_open=5.0, latest_price=5.0),
    }
    results = screener.get_gappers()
    assert len(results) == 1
    assert results[0].symbol == "GOOD"


def test_get_gappers_excludes_non_tradable(screener, mock_client):
    mock_client.get_assets.return_value = [
        {"symbol": "LOCKED", "exchange": "NASDAQ", "tradable": False},
    ]
    mock_client.get_snapshots.return_value = {}
    results = screener.get_gappers()
    assert results == []


def test_get_gappers_sorted_by_gap_descending(screener, mock_client):
    mock_client.get_assets.return_value = [
        {"symbol": "A", "exchange": "NASDAQ", "tradable": True},
        {"symbol": "B", "exchange": "NASDAQ", "tradable": True},
        {"symbol": "C", "exchange": "NASDAQ", "tradable": True},
    ]
    mock_client.get_snapshots.return_value = {
        "A": _make_snap(prev_close=10.0, daily_open=11.5, latest_price=11.5),  # 15%
        "B": _make_snap(prev_close=10.0, daily_open=13.0, latest_price=13.0),  # 30%
        "C": _make_snap(prev_close=10.0, daily_open=11.2, latest_price=11.2),  # 12%
    }
    results = screener.get_gappers()
    assert [r.symbol for r in results] == ["B", "A", "C"]


def test_get_gappers_skips_missing_price_data(screener, mock_client):
    mock_client.get_assets.return_value = [
        {"symbol": "NODATA", "exchange": "NASDAQ", "tradable": True},
    ]
    mock_client.get_snapshots.return_value = {
        "NODATA": {"prevDailyBar": {}, "dailyBar": {}, "latestTrade": {}},
    }
    results = screener.get_gappers()
    assert results == []


def test_get_gappers_batches_snapshots(screener, mock_client, settings):
    # With batch_size=100 and 150 symbols, should call get_snapshots twice
    assets = [{"symbol": f"S{i}", "exchange": "NASDAQ", "tradable": True} for i in range(150)]
    mock_client.get_assets.return_value = assets
    mock_client.get_snapshots.return_value = {}
    screener.get_gappers()
    assert mock_client.get_snapshots.call_count == 2


def test_get_gappers_propagates_broker_error(screener, mock_client):
    mock_client.get_assets.side_effect = GatewayError("service down", status_code=503)
    with pytest.raises(GatewayError):
        screener.get_gappers()


def test_get_gappers_empty_universe(screener, mock_client):
    mock_client.get_assets.return_value = []
    results = screener.get_gappers()
    assert results == []
