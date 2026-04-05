"""
Execution layer dataclasses.

These are internal models used by OrderManager and PositionMonitor.
They are separate from broker/models.py (which mirrors Alpaca's wire format)
so the execution layer can evolve independently of the broker API.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderRequest:
    """Describes an order we want to place.

    stop_price and take_profit_price are intentionally absent — they cannot
    be known before the market order fills.  OrderManager.execute() recomputes
    them from the actual fill price before placing the bracket orders.
    """
    symbol: str
    qty: int           # whole shares
    entry_price: float # pre-fill estimate; used only as fallback if fill price is unavailable


@dataclass(frozen=True)
class OrderStatus:
    """State of a placed order as returned by Alpaca."""
    order_id: str
    symbol: str
    status: str               # pending_new / new / filled / canceled / etc.
    filled_qty: float
    filled_avg_price: float | None


@dataclass(frozen=True)
class PositionState:
    """Current state of an open position."""
    symbol: str
    qty: float
    entry_price: float
    current_price: float
    unrealized_pl: float
    stop_price: float         # stop-loss level being watched
    take_profit_price: float  # take-profit level being watched
    stop_order_id: str        # Alpaca order ID of the stop-loss order
    tp_order_id: str          # Alpaca order ID of the take-profit order
