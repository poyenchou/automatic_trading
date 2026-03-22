from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenerResult:
    """A single screener result passed downstream to strategy code."""
    symbol: str
    volume: float
