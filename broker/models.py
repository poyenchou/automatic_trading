from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthStatus(BaseModel):
    authenticated: bool
    competing: bool = False
    connected: bool = False
    message: str = ""
    mac: str = ""


# ---------------------------------------------------------------------------
# Contracts / search
# ---------------------------------------------------------------------------


class ContractResult(BaseModel):
    conid: int
    symbol: str
    company_name: str = Field("", alias="companyName")
    exchange: str = ""
    instrument_type: str = Field("", alias="instrumentType")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class ScannerParams(BaseModel):
    instrument: str = "STK"
    location: str = "STK.US.MAJOR"
    scan_code: str = "TOP_PERC_GAIN"
    sec_type: str = Field("STK", alias="secType")
    filters: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ScannerRow(BaseModel):
    conid: int
    symbol: str
    pct_change: float = Field(0.0, alias="pctChange")
    last_price: float = Field(0.0, alias="last")
    volume: float = 0.0

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


class MarketDataSnapshot(BaseModel):
    conid: int
    last_price: float | None = Field(None, alias="31")
    bid: float | None = Field(None, alias="84")
    ask: float | None = Field(None, alias="86")
    volume: float | None = Field(None, alias="7762")
    pct_change: float | None = Field(None, alias="82")

    model_config = {"populate_by_name": True}


class OHLCVBar(BaseModel):
    timestamp: datetime
    open: float = Field(alias="o")
    high: float = Field(alias="h")
    low: float = Field(alias="l")
    close: float = Field(alias="c")
    volume: float = Field(alias="v")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MKT = "MKT"
    LMT = "LMT"
    STP = "STP"
    STP_LMT = "STP_LMT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"


class OrderRequest(BaseModel):
    conid: int
    order_type: OrderType = Field(alias="orderType")
    side: OrderSide
    quantity: float = Field(alias="quantity")
    tif: TimeInForce = TimeInForce.DAY
    price: float | None = None
    aux_price: float | None = Field(None, alias="auxPrice")
    ref_id: str = Field("", alias="cOID")

    model_config = {"populate_by_name": True}

    def to_gateway_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "conid": self.conid,
            "orderType": self.order_type.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "tif": self.tif.value,
        }
        if self.price is not None:
            d["price"] = self.price
        if self.aux_price is not None:
            d["auxPrice"] = self.aux_price
        if self.ref_id:
            d["cOID"] = self.ref_id
        return d


class OrderResponse(BaseModel):
    order_id: str = Field(alias="orderId")
    local_order_id: str = Field("", alias="localOrderId")
    order_status: str = Field("", alias="orderStatus")

    model_config = {"populate_by_name": True}


class OrderStatus(BaseModel):
    order_id: str = Field(alias="orderId")
    symbol: str = ""
    side: str = ""
    quantity: float = Field(0.0, alias="totalSize")
    filled: float = Field(0.0, alias="filledQuantity")
    status: str = ""
    price: float | None = None
    avg_price: float | None = Field(None, alias="avgPrice")
    conid: int = 0

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


class PositionState(BaseModel):
    conid: int
    symbol: str = ""
    position: float = 0.0
    avg_cost: float = Field(0.0, alias="avgCost")
    mkt_price: float = Field(0.0, alias="mktPrice")
    mkt_value: float = Field(0.0, alias="mktValue")
    # Set by OrderManager at entry time for TP/SL tracking
    take_profit_price: float | None = None
    stop_loss_price: float | None = None

    model_config = {"populate_by_name": True}
