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
