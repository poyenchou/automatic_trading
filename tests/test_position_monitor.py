"""
Tests for PositionMonitor.
All broker calls are mocked and poll_interval_seconds=0 to avoid sleeping.
"""

from unittest.mock import MagicMock

import pytest

from broker.exceptions import BrokerError
from broker.models import OrderResponse, PositionResponse
from execution.models import PositionState
from execution.position_monitor import PositionMonitor


# ── Helpers ──────────────────────────────────────────────────────────────────

def _state(
    symbol: str = "X",
    stop_order_id: str = "stop-1",
    tp_order_id: str = "tp-1",
) -> PositionState:
    return PositionState(
        symbol=symbol,
        qty=100.0,
        entry_price=10.50,
        current_price=10.50,
        unrealized_pl=0.0,
        stop_price=10.40,
        take_profit_price=10.70,
        stop_order_id=stop_order_id,
        tp_order_id=tp_order_id,
    )


def _order(order_id: str, status: str) -> OrderResponse:
    return OrderResponse(id=order_id, symbol="X", status=status, qty=100)


def _position(symbol: str = "X", current_price: float = 10.55) -> PositionResponse:
    return PositionResponse(
        symbol=symbol, qty=100.0, avg_entry_price=10.50,
        current_price=current_price, unrealized_pl=5.0, side="long",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPositionMonitorTP:
    def test_returns_tp_when_tp_order_filled(self):
        client = MagicMock()
        client.get_position.return_value = None
        client.get_order.side_effect = [
            _order("tp-1", "filled"),   # TP check in _determine_outcome
        ]
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        result = monitor.monitor(_state())
        assert result == "tp"

    def test_cancels_stop_order_when_tp_fills(self):
        client = MagicMock()
        client.get_position.return_value = None
        client.get_order.side_effect = [
            _order("tp-1", "filled"),
        ]
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        monitor.monitor(_state())
        client.cancel_order.assert_called_once_with("stop-1")

    def test_returns_sl_when_stop_order_filled(self):
        client = MagicMock()
        client.get_position.return_value = None
        client.get_order.side_effect = [
            _order("tp-1",   "canceled"),  # TP not filled
            _order("stop-1", "filled"),    # SL filled
        ]
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        result = monitor.monitor(_state())
        assert result == "sl"

    def test_cancels_tp_order_when_stop_fills(self):
        client = MagicMock()
        client.get_position.return_value = None
        client.get_order.side_effect = [
            _order("tp-1",   "canceled"),
            _order("stop-1", "filled"),
        ]
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        monitor.monitor(_state())
        client.cancel_order.assert_called_once_with("tp-1")

    def test_cancel_failure_does_not_raise(self):
        """If the order is already gone, cancel_order raises — should be swallowed."""
        client = MagicMock()
        client.get_position.return_value = None
        client.get_order.side_effect = [_order("tp-1", "filled")]
        client.cancel_order.side_effect = BrokerError("already gone")
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        result = monitor.monitor(_state())   # must not raise
        assert result == "tp"


class TestPositionMonitorStillOpen:
    def test_continues_polling_while_position_open(self):
        client = MagicMock()
        # First two polls: position open. Third poll: gone, TP filled.
        client.get_position.side_effect = [
            _position(), _position(), None
        ]
        # Bracket orders alive during open polls, then TP filled on close check
        client.get_order.side_effect = [
            _order("stop-1", "new"),   # poll 1: SL alive
            _order("tp-1",   "new"),   # poll 1: TP alive
            _order("stop-1", "new"),   # poll 2: SL alive
            _order("tp-1",   "new"),   # poll 2: TP alive
            _order("tp-1",   "filled"),# outcome check: TP filled
        ]
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        result = monitor.monitor(_state())
        assert result == "tp"
        assert client.get_position.call_count == 3


class TestPositionMonitorManualClose:
    def test_closes_manually_when_brackets_missing(self):
        client = MagicMock()
        # Position still open but both bracket orders missing
        client.get_position.return_value = _position()
        client.get_order.side_effect = BrokerError("not found")
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        result = monitor.monitor(_state())
        assert result == "manual"
        client.close_position.assert_called_once_with("X")

    def test_manual_close_failure_is_logged_not_raised(self):
        client = MagicMock()
        client.get_position.return_value = _position()
        client.get_order.side_effect = BrokerError("not found")
        client.close_position.side_effect = BrokerError("close failed")
        monitor = PositionMonitor(client, poll_interval_seconds=0)
        # Should not raise even if close_position fails
        result = monitor.monitor(_state())
        assert result == "manual"
