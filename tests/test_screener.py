"""Unit tests for TopMoversScreener (market_data/screener.py)."""

import pytest
from unittest.mock import MagicMock

from broker.client import AlpacaClient
from broker.exceptions import GatewayError
from broker.models import ScannerRow
from market_data.models import ScreenerResult
from market_data.screener import TopMoversScreener


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock(spec=AlpacaClient)


@pytest.fixture
def screener(mock_client, settings) -> TopMoversScreener:
    return TopMoversScreener(client=mock_client, settings=settings)


def test_get_top_movers_returns_screener_results(screener, mock_client):
    mock_client.get_top_movers.return_value = (
        [
            ScannerRow(symbol="AAPL", volume=50_000_000, trade_count=234567),
            ScannerRow(symbol="TSLA", volume=30_000_000, trade_count=123456),
        ],
        "2026-03-20T23:59:00Z",
    )
    results = screener.get_top_movers()

    assert len(results) == 2
    assert all(isinstance(r, ScreenerResult) for r in results)
    assert results[0].symbol == "AAPL"
    assert results[0].volume == 50_000_000
    assert results[1].symbol == "TSLA"


def test_get_top_movers_uses_num_movers_from_settings(screener, mock_client, settings):
    mock_client.get_top_movers.return_value = ([], "")
    screener.get_top_movers()
    mock_client.get_top_movers.assert_called_once_with(top=settings.num_movers)


def test_get_top_movers_empty_response(screener, mock_client):
    mock_client.get_top_movers.return_value = ([], "")
    results = screener.get_top_movers()
    assert results == []


def test_get_top_movers_excludes_trade_count(screener, mock_client):
    mock_client.get_top_movers.return_value = (
        [ScannerRow(symbol="NVDA", volume=10_000_000, trade_count=99999)],
        "",
    )
    result = screener.get_top_movers()[0]
    assert not hasattr(result, "trade_count")


def test_get_top_movers_propagates_broker_error(screener, mock_client):
    mock_client.get_top_movers.side_effect = GatewayError("service down", status_code=503)
    with pytest.raises(GatewayError):
        screener.get_top_movers()
