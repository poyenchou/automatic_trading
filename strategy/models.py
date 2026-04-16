from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    BUY = "BUY"
    NONE = "NONE"


@dataclass(frozen=True)
class SignalResult:
    symbol: str
    direction: Direction
    # Human-readable explanation of why this signal fired (or did not)
    reason: str
    # Chart-based stop: low of the dip candle (None when not applicable)
    dip_low: float | None = None
    # Suggested stop-loss price: dip_low minus a small buffer (None → use fixed cents)
    stop_price: float | None = None
