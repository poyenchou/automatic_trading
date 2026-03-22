from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Alpaca API
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_trading_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Safety
    paper_trading: bool = True

    # Screener
    num_movers: int = 5
    exchange: str = "NYSE"

    # Strategy parameters
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    volume_spike_multiplier: float = 2.0

    # Risk management
    take_profit_pct: float = 2.0
    stop_loss_pct: float = 1.0
    max_position_size_usd: float = 1000.0

    # Operational
    poll_interval_seconds: int = 30
    log_level: str = "INFO"
    log_format: str = "json"
