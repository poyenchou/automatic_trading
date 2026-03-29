"""
Float data fetcher using Yahoo Finance (yfinance).

Alpaca does not provide float shares data, so we use yfinance as a secondary
data source for this one piece of fundamental information.

Float = number of shares available for public trading (excludes insider/restricted
shares). Ross Cameron targets stocks with < 20M float because low float means
a small amount of buying creates a large price move.

NOTE — PROTOTYPING ONLY:
    yfinance is an unofficial library that scrapes Yahoo Finance. It can break
    without warning if Yahoo changes their API. It is acceptable for local
    development and paper trading, but before going live consider switching to
    a reliable paid endpoint:

      - Financial Modeling Prep: GET /v4/shares_float?symbol=X  (free tier: 250 req/day)
      - Polygon.io:              GET /v3/reference/tickers/{symbol}
      - SEC-API:                 derives float from SEC filings (most accurate)

    To swap providers, only this file needs to change — nothing else in the
    codebase depends on how float is fetched.
"""

import structlog
import yfinance as yf

log = structlog.get_logger(__name__)

# Ross Cameron's typical float threshold
DEFAULT_MAX_FLOAT = 20_000_000


class FloatFetcher:
    def get_float_shares(self, symbol: str) -> int | None:
        """
        Fetch the public float (shares available to trade) for a symbol.

        Returns None if Yahoo Finance does not have float data for the symbol
        (common for very new listings or OTC stocks).

        Args:
            symbol: Stock ticker, e.g. "AAPL".
        """
        try:
            info = yf.Ticker(symbol).info
            float_shares = info.get("floatShares")
            log.debug("float_filter.get_float_shares", symbol=symbol, float_shares=float_shares)
            return int(float_shares) if float_shares is not None else None
        except Exception as exc:
            log.warning("float_filter.get_float_shares.failed", symbol=symbol, error=str(exc))
            return None

    def is_low_float(self, symbol: str, max_float: int = DEFAULT_MAX_FLOAT) -> bool:
        """
        True if the symbol's float is at or below max_float.

        Returns False (conservative) if float data is unavailable, so the
        strategy skips the symbol rather than trading blind.

        Args:
            symbol:    Stock ticker.
            max_float: Maximum acceptable float (default 20M shares).
        """
        shares = self.get_float_shares(symbol)
        if shares is None:
            log.warning("float_filter.is_low_float.no_data", symbol=symbol)
            return False
        return shares <= max_float
