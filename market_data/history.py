import pandas as pd
import structlog

from broker.client import AlpacaClient

log = structlog.get_logger(__name__)

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


class HistoricalDataFetcher:
    def __init__(self, client: AlpacaClient) -> None:
        self._client = client

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str = "5Min",
        start: str | None = None,
        end: str | None = None,
        limit: int = 200,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV bars for a symbol and return a clean DataFrame.

        Returns a DataFrame with a UTC-aware DatetimeIndex named "timestamp"
        and float64 columns: open, high, low, close, volume.
        Returns an empty DataFrame (correct schema) if no bars are available.
        BrokerError subclasses propagate to the caller.
        """
        if not symbol or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not timeframe:
            raise ValueError("timeframe must be a non-empty string")
        if limit <= 0:
            raise ValueError("limit must be positive")

        bars = self._client.get_historical_bars(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
        )
        log.debug("history.fetch_bars", symbol=symbol, count=len(bars))

        if not bars:
            return _empty_df()

        df = pd.DataFrame([{
            "timestamp": bar.timestamp,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        } for bar in bars])

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
        df[_OHLCV_COLS] = df[_OHLCV_COLS].astype("float64")
        df = df.sort_index()
        df = df.dropna()
        return df


def _empty_df() -> pd.DataFrame:
    df = pd.DataFrame(columns=_OHLCV_COLS)
    df.index = pd.DatetimeIndex([], name="timestamp", tz="UTC")
    df[_OHLCV_COLS] = df[_OHLCV_COLS].astype("float64")
    return df
