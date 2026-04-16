"""
OrderManager — sizes and places orders with stop-loss and take-profit brackets.

Position sizing:
  - Risk a fixed % of account equity per trade (e.g. 1%)
  - Stop = chart-based (dip_low − buffer) when provided; else fixed cents from settings
  - qty = (equity * risk_pct) / stop_distance
  - Take profit = fill_price + 2 * (fill_price − stop_price)  (2:1 R/R)
  - Capped at settings.max_shares

Entry flow:
  1. Compute qty from account equity
  2. Place market order (entry)
  3. Wait for fill to get actual fill price
  4. Place stop-loss stop order below fill price
  5. Place take-profit limit order above fill price
  6. Return PositionState

No indicator logic in this module.
"""

import time

import structlog

from broker.client import AlpacaClient
from broker.exceptions import BrokerError
from config.settings import Settings
from execution.models import OrderRequest, OrderStatus, PositionState

log = structlog.get_logger(__name__)

# How long to poll for fill confirmation after placing a market order
_FILL_POLL_INTERVAL = 1.0   # seconds between polls
_FILL_TIMEOUT       = 30.0  # seconds before giving up


class OrderManager:
    def __init__(self, client: AlpacaClient, settings: Settings) -> None:
        assert settings.paper_trading is True, (
            "OrderManager refuses to run outside paper trading mode. "
            "Set PAPER_TRADING=true in your .env file."
        )
        self._client   = client
        self._settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_order_request(
        self,
        symbol: str,
        current_price: float,
        stop_price: float | None = None,
    ) -> OrderRequest:
        """
        Compute order parameters from account equity and settings.

        Args:
            symbol:        Stock ticker.
            current_price: Current ask/last price used to estimate entry.
            stop_price:    Chart-based stop level from strategy (optional).
                           When provided, stop distance = current_price − stop_price.
                           When None, stop distance = settings.stop_loss_cents.

        Returns:
            OrderRequest with qty computed from account equity.
            TP is computed later from the actual fill price.
        """
        account      = self._client.get_account()
        equity       = account.equity
        risk_dollars = equity * self._settings.risk_per_trade_pct

        if stop_price is not None:
            stop_distance = max(current_price - stop_price, self._settings.stop_loss_cents)
        else:
            stop_distance = self._settings.stop_loss_cents

        qty = int(risk_dollars / stop_distance)
        qty = max(1, min(qty, self._settings.max_shares))

        log.info(
            "order_manager.build_order_request",
            symbol=symbol,
            equity=equity,
            risk_dollars=risk_dollars,
            stop_distance=stop_distance,
            chart_stop=stop_price,
            qty=qty,
        )

        return OrderRequest(
            symbol=symbol,
            qty=qty,
            entry_price=current_price,
            stop_price=stop_price,
        )

    def execute(self, request: OrderRequest) -> PositionState:
        """
        Place entry order, wait for fill, then attach TP/SL brackets.

        Args:
            request: OrderRequest produced by build_order_request().

        Returns:
            PositionState with fill price and bracket order IDs.

        Raises:
            BrokerError: if the entry order cannot be placed or does not fill.
        """
        # ── Step 1: Place market entry ────────────────────────────────────
        log.info("order_manager.placing_entry", symbol=request.symbol, qty=request.qty)
        entry_order = self._client.place_market_order(request.symbol, request.qty, side="buy")
        log.info("order_manager.entry_placed", order_id=entry_order.id, status=entry_order.status)

        # ── Step 2: Wait for fill ─────────────────────────────────────────
        fill_status = self._wait_for_fill(entry_order.id)
        fill_price  = fill_status.filled_avg_price or request.entry_price

        # Stop: use chart-based level when available, else fixed cents below fill
        if request.stop_price is not None:
            stop_price = round(request.stop_price, 2)
        else:
            stop_price = round(fill_price - self._settings.stop_loss_cents, 2)

        # TP: always 2:1 R/R relative to the actual stop distance from fill
        stop_distance     = fill_price - stop_price
        take_profit_price = round(fill_price + 2 * stop_distance, 2)

        log.info(
            "order_manager.filled",
            symbol=request.symbol,
            fill_price=fill_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
        )

        # ── Step 3: Place stop-loss ───────────────────────────────────────
        stop_order = self._client.place_stop_order(
            symbol=request.symbol,
            qty=request.qty,
            stop_price=stop_price,
        )
        log.info("order_manager.stop_placed", order_id=stop_order.id, stop_price=stop_price)

        # ── Step 4: Place take-profit ─────────────────────────────────────
        tp_order = self._client.place_limit_order(
            symbol=request.symbol,
            qty=request.qty,
            limit_price=take_profit_price,
        )
        log.info("order_manager.tp_placed", order_id=tp_order.id, tp_price=take_profit_price)

        return PositionState(
            symbol=request.symbol,
            qty=float(request.qty),
            entry_price=fill_price,
            current_price=fill_price,
            unrealized_pl=0.0,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            stop_order_id=stop_order.id,
            tp_order_id=tp_order.id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wait_for_fill(self, order_id: str) -> OrderStatus:
        """Poll until the order is filled or timeout is reached."""
        elapsed = 0.0
        while elapsed < _FILL_TIMEOUT:
            order = self._client.get_order(order_id)
            if order.status == "filled":
                return OrderStatus(
                    order_id=order.id,
                    symbol=order.symbol,
                    status=order.status,
                    filled_qty=order.filled_qty,
                    filled_avg_price=order.filled_avg_price,
                )
            if order.status in ("canceled", "expired", "rejected"):
                raise BrokerError(
                    f"Order {order_id} ended with status '{order.status}' before filling."
                )
            time.sleep(_FILL_POLL_INTERVAL)
            elapsed += _FILL_POLL_INTERVAL

        raise BrokerError(
            f"Order {order_id} did not fill within {_FILL_TIMEOUT}s."
        )
