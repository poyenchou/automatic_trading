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

    take_profit_price is absent — it is always computed by OrderManager.execute()
    from the actual fill price (2:1 R/R relative to stop distance).

    stop_price is optional: when provided (chart-based stop from strategy), it
    overrides the fixed stop_loss_cents setting. When None, the fixed setting is used.
    """
    symbol: str
    qty: int                        # whole shares
    entry_price: float              # pre-fill estimate; used only as fallback if fill price unavailable
    stop_price: float | None = None # chart-based stop (dip_low − buffer); None → use fixed cents


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
