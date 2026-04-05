from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountInfo(BaseModel):
    id: str
    status: str = ""
    currency: str = ""
    buying_power: float = 0.0
    equity: float = 0.0

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------


class ScannerRow(BaseModel):
    symbol: str
    volume: float = 0.0
    trade_count: int = 0

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Historical bars
# ---------------------------------------------------------------------------


class OHLCVBar(BaseModel):
    timestamp: datetime = Field(alias="t")
    open: float = Field(alias="o")
    high: float = Field(alias="h")
    low: float = Field(alias="l")
    close: float = Field(alias="c")
    volume: float = Field(alias="v")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderResponse(BaseModel):
    """Alpaca order response from POST /v2/orders or GET /v2/orders/{id}."""
    id: str
    symbol: str = ""
    status: str = ""                    # pending_new, new, filled, canceled, etc.
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    side: str = ""                      # buy / sell
    order_type: str = Field("", alias="type")
    qty: float = 0.0

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


class PositionResponse(BaseModel):
    """Alpaca position from GET /v2/positions/{symbol}."""
    symbol: str
    qty: float = 0.0
    avg_entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pl: float = 0.0
    side: str = ""                      # long / short

    model_config = {"populate_by_name": True}
