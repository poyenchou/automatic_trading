"""Unit tests for IBKRClient (broker/client.py)."""

from unittest.mock import MagicMock

import httpx
import pytest
import respx

from broker.auth import SessionManager
from broker.client import IBKRClient, _TokenBucketLimiter
from broker.exceptions import AuthError, GatewayError, OrderRejectedError, RateLimitError
from broker.models import (
    MarketDataSnapshot,
    OHLCVBar,
    OrderRequest,
    OrderSide,
    OrderType,
    PositionState,
    ScannerParams,
    ScannerRow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=SessionManager)


@pytest.fixture
def client(settings, mock_session) -> IBKRClient:
    return IBKRClient(settings=settings, session=mock_session)


# ---------------------------------------------------------------------------
# _TokenBucketLimiter
# ---------------------------------------------------------------------------


def test_token_bucket_does_not_block_when_tokens_available():
    import time

    limiter = _TokenBucketLimiter(rate=100)
    start = time.monotonic()
    for _ in range(10):
        limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, "Should acquire 10 tokens from a full bucket near-instantly"


def test_token_bucket_throttles_when_empty():
    import time

    limiter = _TokenBucketLimiter(rate=20)
    # Drain the bucket
    for _ in range(20):
        limiter.acquire()
    # Next acquire should sleep briefly
    start = time.monotonic()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.01, "Should have slept to respect rate limit"


# ---------------------------------------------------------------------------
# _handle_response — error mapping
# ---------------------------------------------------------------------------


@respx.mock
def test_get_raises_auth_error_on_401(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(AuthError):
        client._get("/iserver/auth/status")


@respx.mock
def test_get_raises_rate_limit_error_on_429(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"})
    )
    with pytest.raises(RateLimitError):
        client._get("/iserver/auth/status")


@respx.mock
def test_post_raises_order_rejected_on_400(client, settings):
    respx.post(f"{settings.gateway_url}/iserver/account/DU123456/orders").mock(
        return_value=httpx.Response(400, json={"error": "Invalid order"})
    )
    with pytest.raises(OrderRejectedError, match="Invalid order"):
        client._post("/iserver/account/DU123456/orders", body={})


@respx.mock
def test_get_raises_gateway_error_on_503(client, settings):
    # tenacity will retry 3 times; mock all attempts
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    with pytest.raises(GatewayError) as exc_info:
        client._get("/iserver/auth/status")
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# get_auth_status
# ---------------------------------------------------------------------------


@respx.mock
def test_get_auth_status(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/auth/status").mock(
        return_value=httpx.Response(
            200, json={"authenticated": True, "connected": True, "competing": False}
        )
    )
    status = client.get_auth_status()
    assert status.authenticated is True
    assert status.connected is True


# ---------------------------------------------------------------------------
# get_market_data_snapshot
# ---------------------------------------------------------------------------


@respx.mock
def test_get_market_data_snapshot(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/marketdata/snapshot").mock(
        return_value=httpx.Response(
            200,
            json=[{"conid": 265598, "31": 175.50, "84": 175.48, "86": 175.52}],
        )
    )
    snapshots = client.get_market_data_snapshot([265598])
    assert len(snapshots) == 1
    assert isinstance(snapshots[0], MarketDataSnapshot)
    assert snapshots[0].conid == 265598
    assert snapshots[0].last_price == 175.50


@respx.mock
def test_get_market_data_snapshot_empty_response(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/marketdata/snapshot").mock(
        return_value=httpx.Response(200, json={})
    )
    result = client.get_market_data_snapshot([265598])
    assert result == []


# ---------------------------------------------------------------------------
# get_historical_data
# ---------------------------------------------------------------------------


@respx.mock
def test_get_historical_data(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/marketdata/history").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"t": 1_700_000_000_000, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1_000_000},
                    {"t": 1_700_000_300_000, "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.0, "v": 900_000},
                ]
            },
        )
    )
    bars = client.get_historical_data(conid=265598, period="1d", bar="5mins")
    assert len(bars) == 2
    assert isinstance(bars[0], OHLCVBar)
    assert bars[0].open == 100.0
    assert bars[0].close == 100.5


@respx.mock
def test_get_historical_data_empty(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/marketdata/history").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    bars = client.get_historical_data(conid=265598)
    assert bars == []


# ---------------------------------------------------------------------------
# get_scanner_results
# ---------------------------------------------------------------------------


@respx.mock
def test_get_scanner_results(client, settings):
    respx.post(f"{settings.gateway_url}/iserver/scanner/run").mock(
        return_value=httpx.Response(
            200,
            json={
                "contracts": [
                    {"conid": 265598, "symbol": "AAPL", "pctChange": 5.2, "last": 178.0, "volume": 5_000_000},
                    {"conid": 4815747, "symbol": "TSLA", "pctChange": 4.8, "last": 250.0, "volume": 3_000_000},
                ]
            },
        )
    )
    rows = client.get_scanner_results(ScannerParams())
    assert len(rows) == 2
    assert isinstance(rows[0], ScannerRow)
    assert rows[0].symbol == "AAPL"
    assert rows[0].pct_change == 5.2


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------


@respx.mock
def test_place_order(client, settings):
    respx.post(f"{settings.gateway_url}/iserver/account/DU123456/orders").mock(
        return_value=httpx.Response(
            200,
            json=[{"orderId": "1234567890", "localOrderId": "local-1", "orderStatus": "Submitted"}],
        )
    )
    order = OrderRequest(
        conid=265598,
        orderType=OrderType.MKT,
        side=OrderSide.BUY,
        quantity=10,
    )
    response = client.place_order("DU123456", order)
    assert response.order_id == "1234567890"
    assert response.order_status == "Submitted"


# ---------------------------------------------------------------------------
# get_orders
# ---------------------------------------------------------------------------


@respx.mock
def test_get_orders(client, settings):
    respx.get(f"{settings.gateway_url}/iserver/account/DU123456/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "orderId": "111",
                        "symbol": "AAPL",
                        "side": "BUY",
                        "totalSize": 10,
                        "filledQuantity": 10,
                        "status": "Filled",
                    }
                ]
            },
        )
    )
    orders = client.get_orders("DU123456")
    assert len(orders) == 1
    assert orders[0].order_id == "111"
    assert orders[0].status == "Filled"


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------


@respx.mock
def test_get_positions(client, settings):
    respx.get(f"{settings.gateway_url}/portfolio/DU123456/positions/0").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "conid": 265598,
                    "symbol": "AAPL",
                    "position": 10.0,
                    "avgCost": 170.0,
                    "mktPrice": 175.0,
                    "mktValue": 1750.0,
                }
            ],
        )
    )
    positions = client.get_positions("DU123456")
    assert len(positions) == 1
    assert isinstance(positions[0], PositionState)
    assert positions[0].symbol == "AAPL"
    assert positions[0].avg_cost == 170.0


@respx.mock
def test_get_positions_empty(client, settings):
    respx.get(f"{settings.gateway_url}/portfolio/DU123456/positions/0").mock(
        return_value=httpx.Response(200, json=[])
    )
    positions = client.get_positions("DU123456")
    assert positions == []


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


@respx.mock
def test_cancel_order(client, settings):
    respx.delete(
        f"{settings.gateway_url}/iserver/account/DU123456/order/9999"
    ).mock(return_value=httpx.Response(200, json={"msg": "Request was submitted"}))
    client.cancel_order("DU123456", "9999")  # should not raise


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


@respx.mock
def test_get_retries_on_503_then_succeeds(client, settings):
    route = respx.get(f"{settings.gateway_url}/iserver/auth/status")
    route.side_effect = [
        httpx.Response(503, text="unavailable"),
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, json={"authenticated": True, "connected": True}),
    ]
    # Should succeed on the third attempt without raising
    status = client.get_auth_status()
    assert status.authenticated is True
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# context manager
# ---------------------------------------------------------------------------


def test_client_context_manager(settings, mock_session):
    with IBKRClient(settings=settings, session=mock_session) as c:
        assert c is not None
    # After __exit__ the underlying httpx client is closed (no assertion needed —
    # just verifying no exception is raised)
