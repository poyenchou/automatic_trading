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
    pct_change: float = Field(0.0, alias="percent_change")
    price: float = 0.0
    volume: float = 0.0

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
