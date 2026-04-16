"""
Tests for OrderManager.
All broker calls are mocked — no network required.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from broker.models import AccountInfo, OrderResponse
from config.settings import Settings
from execution.models import OrderRequest, PositionState
from execution.order_manager import OrderManager


# ── Helpers ──────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    base = dict(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        paper_trading=True,
        risk_per_trade_pct=0.01,
        stop_loss_cents=0.10,
        max_shares=1000,
    )
    base.update(overrides)
    return Settings(**base)


def _mock_client(
    equity: float = 10_000.0,
    fill_status: str = "filled",
    fill_price: float = 10.50,
) -> MagicMock:
    client = MagicMock()
    client.get_account.return_value = AccountInfo(
        id="acc1", status="ACTIVE", equity=equity, buying_power=equity
    )

    entry_order = OrderResponse(id="entry-1", symbol="X", status="new", qty=100, filled_qty=0)
    filled_order = OrderResponse(
        id="entry-1", symbol="X", status=fill_status,
        qty=100, filled_qty=100, filled_avg_price=fill_price,
    )
    # First call returns "new", second returns "filled"
    client.get_order.side_effect = [entry_order, filled_order]

    client.place_market_order.return_value = entry_order
    client.place_stop_order.return_value   = OrderResponse(id="stop-1", symbol="X", status="new", qty=100)
    client.place_limit_order.return_value  = OrderResponse(id="tp-1",   symbol="X", status="new", qty=100)
    return client


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOrderManagerInit:
    def test_refuses_live_trading(self):
        client = MagicMock()
        with pytest.raises(AssertionError, match="paper trading"):
            OrderManager(client, _settings(paper_trading=False))

    def test_accepts_paper_trading(self):
        client = MagicMock()
        om = OrderManager(client, _settings())
        assert om is not None


class TestBuildOrderRequest:
    def test_qty_computed_from_equity_and_risk(self):
        # equity=10000, risk=1% → $100 at risk, stop=$0.10 → qty=1000
        client = _mock_client(equity=10_000.0)
        om = OrderManager(client, _settings(risk_per_trade_pct=0.01, stop_loss_cents=0.10))
        req = om.build_order_request("X", current_price=10.0)
        assert req.qty == 1000

    def test_qty_capped_at_max_shares(self):
        # equity=100000 → uncapped qty=10000, but max_shares=500
        client = _mock_client(equity=100_000.0)
        om = OrderManager(client, _settings(max_shares=500))
        req = om.build_order_request("X", current_price=10.0)
        assert req.qty == 500

    def test_qty_minimum_one(self):
        # tiny equity → qty rounds to 0, should be floored to 1
        client = _mock_client(equity=1.0)
        om = OrderManager(client, _settings(risk_per_trade_pct=0.01, stop_loss_cents=0.10))
        req = om.build_order_request("X", current_price=10.0)
        assert req.qty >= 1

    def test_entry_price_preserved(self):
        client = _mock_client(equity=10_000.0)
        om = OrderManager(client, _settings())
        req = om.build_order_request("X", current_price=10.0)
        assert req.entry_price == pytest.approx(10.0)

    def test_symbol_preserved(self):
        client = _mock_client()
        om = OrderManager(client, _settings())
        req = om.build_order_request("MEME", current_price=5.0)
        assert req.symbol == "MEME"


class TestExecute:
    def test_places_market_order(self):
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings())
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0)
        om.execute(req)
        client.place_market_order.assert_called_once_with("X", 100, side="buy")

    def test_places_stop_and_tp_after_fill(self):
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings(stop_loss_cents=0.10))
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0)
        om.execute(req)
        client.place_stop_order.assert_called_once()
        client.place_limit_order.assert_called_once()

    def test_stop_price_based_on_fill_price(self):
        # Fill at 10.50 → stop = 10.50 - 0.10 = 10.40
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings(stop_loss_cents=0.10))
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0)
        state = om.execute(req)
        assert state.stop_price == pytest.approx(10.40)

    def test_tp_price_based_on_fill_price(self):
        # Fill at 10.50 → TP = 10.50 + 2*0.10 = 10.70
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings(stop_loss_cents=0.10))
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0)
        state = om.execute(req)
        assert state.take_profit_price == pytest.approx(10.70)

    def test_returns_position_state(self):
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings())
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0)
        state = om.execute(req)
        assert isinstance(state, PositionState)
        assert state.symbol == "X"
        assert state.stop_order_id == "stop-1"
        assert state.tp_order_id == "tp-1"

    def test_chart_stop_used_when_provided(self):
        # Fill at 10.50, chart stop at 10.20 → stop_distance=0.30, TP=10.50+0.60=11.10
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings(stop_loss_cents=0.10))
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0, stop_price=10.20)
        state = om.execute(req)
        assert state.stop_price == pytest.approx(10.20)
        assert state.take_profit_price == pytest.approx(11.10)

    def test_fixed_stop_used_when_no_chart_stop(self):
        # Fill at 10.50, no chart stop → stop = 10.50 - 0.10 = 10.40, TP = 10.70
        client = _mock_client(fill_price=10.50)
        om = OrderManager(client, _settings(stop_loss_cents=0.10))
        req = OrderRequest(symbol="X", qty=100, entry_price=10.0, stop_price=None)
        state = om.execute(req)
        assert state.stop_price == pytest.approx(10.40)
        assert state.take_profit_price == pytest.approx(10.70)


class TestBuildOrderRequestWithChartStop:
    def test_chart_stop_sizes_position_correctly(self):
        # equity=10000, risk=1% → $100, chart stop distance=0.50 → qty=200
        client = _mock_client(equity=10_000.0)
        om = OrderManager(client, _settings(risk_per_trade_pct=0.01, stop_loss_cents=0.10))
        req = om.build_order_request("X", current_price=10.50, stop_price=10.00)
        assert req.qty == 200  # 100 / 0.50 = 200
        assert req.stop_price == pytest.approx(10.00)

    def test_chart_stop_distance_floored_at_min_cents(self):
        # stop_price > current_price is impossible but chart stop very close to price
        # should not produce zero or negative stop distance — floor at stop_loss_cents
        client = _mock_client(equity=10_000.0)
        om = OrderManager(client, _settings(risk_per_trade_pct=0.01, stop_loss_cents=0.10))
        # current_price=10.50, stop_price=10.49 → distance=0.01 < min 0.10 → use 0.10
        req = om.build_order_request("X", current_price=10.50, stop_price=10.49)
        assert req.qty == 1000  # 100 / 0.10 = 1000 (min cents floor kicks in)
