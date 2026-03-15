import pandas as pd
import pytest

from config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings instance with safe test defaults (no .env required)."""
    return Settings(
        gateway_url="https://localhost:5000/v1/api",
        gateway_verify_ssl=False,
        ibkr_account_id="DU123456",
        paper_trading=True,
        num_movers=5,
        rsi_period=14,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        volume_spike_multiplier=2.0,
        take_profit_pct=2.0,
        stop_loss_pct=1.0,
        max_position_size_usd=1000.0,
        poll_interval_seconds=30,
        log_level="INFO",
        log_format="console",
    )


@pytest.fixture
def sample_bars() -> pd.DataFrame:
    """60 bars of synthetic OHLCV data sufficient for all TA indicators."""
    import numpy as np

    rng = np.random.default_rng(42)
    n = 60
    close = 100 + rng.normal(0, 1, n).cumsum()
    high = close + rng.uniform(0.1, 0.5, n)
    low = close - rng.uniform(0.1, 0.5, n)
    open_ = close + rng.normal(0, 0.2, n)
    volume = rng.integers(500_000, 2_000_000, n).astype(float)
    timestamps = pd.date_range("2024-01-01 09:30", periods=n, freq="5min")

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "timestamp": timestamps,
        }
    )
