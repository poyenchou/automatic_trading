import pandas as pd
import pytest

from config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings instance with safe test defaults (no .env required)."""
    return Settings(
        alpaca_api_key="TESTKEY123",
        alpaca_api_secret="TESTSECRET456",
        alpaca_trading_url="https://paper-api.alpaca.markets",
        alpaca_data_url="https://data.alpaca.markets",
        paper_trading=True,
        # Screener
        gap_min_pct=0.10,
        min_daily_volume=500_000,
        min_stock_price=1.5,
        snapshot_batch_size=100,
        # Momentum strategy
        rsi_period=14,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        # First Dip strategy
        first_dip_min_rel_vol=2.0,
        first_dip_max_float=20_000_000,
        first_dip_ema_period=9,
        first_dip_range_bars=1,
        # Risk management
        risk_per_trade_pct=0.01,
        stop_loss_cents=0.10,
        max_shares=1000,
        # Operational
        poll_interval_seconds=30,
        scan_interval_seconds=300,
        monitor_exit_time="11:00",
        max_concurrent_positions=2,
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
