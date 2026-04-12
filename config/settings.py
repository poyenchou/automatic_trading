from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Alpaca API
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_trading_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Safety
    paper_trading: bool = True

    # Screener
    gap_min_pct: float = 0.10          # minimum gap up % to qualify (0.10 = 10%)
    min_daily_volume: int = 500_000    # minimum pre-market/daily volume to qualify
    snapshot_batch_size: int = 100     # symbols per Alpaca snapshots API call

    # Strategy parameters
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    volume_spike_multiplier: float = 1.5

    # Stock filters
    min_stock_price: float = 1.5   # skip penny stocks below this price

    # Risk management
    # % of account equity to risk per trade (e.g. 0.01 = 1%)
    risk_per_trade_pct: float = 0.01
    # Fixed cents below entry price for stop loss (e.g. 0.10 = $0.10)
    stop_loss_cents: float = 0.10
    # Take profit uses 2:1 R/R — target = entry + 2 * stop_distance (not configurable)
    # Maximum shares per order regardless of position sizing (safety cap)
    max_shares: int = 1000

    # Operational
    poll_interval_seconds: int = 5
    scan_interval_seconds: int = 300    # seconds between signal rescans (default 5 min)
    monitor_exit_time: str = "11:00"      # force-close all positions at this time ET (HH:MM)
    max_concurrent_positions: int = 2
    log_level: str = "INFO"
    log_format: str = "json"
