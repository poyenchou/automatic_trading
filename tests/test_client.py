"""Unit tests for AlpacaClient (broker/client.py)."""

from unittest.mock import MagicMock

import httpx
import pytest
import respx

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, GatewayError, OrderRejectedError, RateLimitError
from broker.models import AccountInfo, OHLCVBar, ScannerRow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_auth() -> MagicMock:
    auth = MagicMock(spec=AlpacaAuth)
    auth.headers.return_value = {
        "APCA-API-KEY-ID": "TESTKEY123",
        "APCA-API-SECRET-KEY": "TESTSECRET456",
    }
    return auth


@pytest.fixture
def client(settings, mock_auth) -> AlpacaClient:
    return AlpacaClient(settings=settings, auth=mock_auth)


# ---------------------------------------------------------------------------
# _handle_response — error mapping
# ---------------------------------------------------------------------------


@respx.mock
def test_get_raises_auth_error_on_401(client, settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(AuthError):
        client._get_trading("/v2/account")


@respx.mock
def test_get_raises_rate_limit_error_on_429(client, settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"})
    )
    with pytest.raises(RateLimitError):
        client._get_trading("/v2/account")


@respx.mock
def test_post_raises_order_rejected_on_400(client, settings):
    respx.post(f"{settings.alpaca_trading_url}/v2/orders").mock(
        return_value=httpx.Response(400, json={"message": "Invalid order"})
    )
    with pytest.raises(OrderRejectedError, match="Invalid order"):
        client._post_trading("/v2/orders", body={})


@respx.mock
def test_get_raises_gateway_error_on_503(client, settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    with pytest.raises(GatewayError) as exc_info:
        client._get_trading("/v2/account")
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------


@respx.mock
def test_get_account(client, settings):
    respx.get(f"{settings.alpaca_trading_url}/v2/account").mock(
        return_value=httpx.Response(
            200,
            json={"id": "PA123456", "status": "ACTIVE", "currency": "USD",
                  "buying_power": "10000.00", "equity": "10000.00"},
        )
    )
    account = client.get_account()
    assert isinstance(account, AccountInfo)
    assert account.id == "PA123456"
    assert account.status == "ACTIVE"


# ---------------------------------------------------------------------------
# get_top_movers
# ---------------------------------------------------------------------------


@respx.mock
def test_get_top_movers(client, settings):
    respx.get(f"{settings.alpaca_data_url}/v1beta1/screener/stocks/most-actives").mock(
        return_value=httpx.Response(
            200,
            json={
                "most_actives": [
                    {"symbol": "AAPL", "volume": 50_000_000, "price": 178.5, "percent_change": 1.83},
                    {"symbol": "TSLA", "volume": 30_000_000, "price": 250.0, "percent_change": -0.5},
                ]
            },
        )
    )
    rows = client.get_top_movers(top=2)
    assert len(rows) == 2
    assert isinstance(rows[0], ScannerRow)
    assert rows[0].symbol == "AAPL"
    assert rows[0].pct_change == 1.83
    assert rows[0].price == 178.5


# ---------------------------------------------------------------------------
# get_historical_bars
# ---------------------------------------------------------------------------


@respx.mock
def test_get_historical_bars(client, settings):
    respx.get(f"{settings.alpaca_data_url}/v2/stocks/AAPL/bars").mock(
        return_value=httpx.Response(
            200,
            json={
                "bars": [
                    {"t": "2024-01-15T14:30:00Z", "o": 100.0, "h": 101.5, "l": 99.5, "c": 101.0, "v": 1_234_567},
                    {"t": "2024-01-15T14:35:00Z", "o": 101.0, "h": 102.0, "l": 100.5, "c": 101.8, "v": 987_654},
                ],
                "symbol": "AAPL",
            },
        )
    )
    bars = client.get_historical_bars("AAPL", timeframe="5Min")
    assert len(bars) == 2
    assert isinstance(bars[0], OHLCVBar)
    assert bars[0].open == 100.0
    assert bars[0].close == 101.0


@respx.mock
def test_get_historical_bars_empty(client, settings):
    respx.get(f"{settings.alpaca_data_url}/v2/stocks/AAPL/bars").mock(
        return_value=httpx.Response(200, json={"bars": [], "symbol": "AAPL"})
    )
    bars = client.get_historical_bars("AAPL")
    assert bars == []
