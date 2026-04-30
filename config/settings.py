from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Alpaca API ────────────────────────────────────────────────────────────
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_trading_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    # ── Safety ────────────────────────────────────────────────────────────────
    paper_trading: bool = True

    # ── Screener (GapScreener — runs once at startup) ─────────────────────────
    gap_min_pct: float = 0.05          # minimum gap up % to qualify (0.05 = 5%)
    min_daily_volume: int = 500_000    # minimum pre-market volume (Ross Cameron: 500K–1M)
    min_stock_price: float = 1.5       # skip penny stocks below this price
    snapshot_batch_size: int = 100     # symbols per Alpaca snapshots API call

    # ── Momentum strategy (RSI + MACD + EMA) ─────────────────────────────────
    # Used by MomentumStrategy only. Not active by default (see run_morning.py).
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ── First Dip strategy (Ross Cameron Gap & Go) ────────────────────────────
    # Used by FirstDipStrategy only.
    first_dip_min_rel_vol: float = 2.0  # minimum relative volume multiplier
    first_dip_max_float: int = 500_000_000  # maximum public float (shares)
    first_dip_ema_period: int = 9          # EMA period used as support line
    first_dip_range_bars: int = 1          # opening range bar count for ORB entry

    # ── Risk management (shared by both strategies) ───────────────────────────
    risk_per_trade_pct: float = 0.01   # % of account equity to risk per trade (1%)
    stop_loss_cents: float = 0.10      # fixed cents below fill price for stop loss
    # Take profit uses 2:1 R/R — target = entry + 2 × stop_distance (not configurable)
    max_shares: int = 1000             # maximum shares per order (safety cap)

    # ── Operational ───────────────────────────────────────────────────────────
    poll_interval_seconds: int = 5        # position monitor poll frequency
    scan_interval_seconds: int = 300      # seconds between signal rescans (5 min)
    monitor_exit_time: str = "11:00"      # force-close all positions at this time ET (HH:MM)
    max_concurrent_positions: int = 2
    log_level: str = "INFO"
    log_format: str = "json"
