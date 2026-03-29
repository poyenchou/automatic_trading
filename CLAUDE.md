# Automatic Trading Bot — Claude Context

## Project Overview
Python automated trading bot using the Alpaca REST API for paper trading. Development is local first, then EC2.

## IMPORTANT RULES
- Implement **ONE phase at a time**.
- Do **NOT** implement future phases.
- Do **NOT** modify unrelated files.
- After implementing a requested phase, **STOP and wait for review**.
- Strategy code must **NOT** import broker modules.
- Execution layer must **NOT** compute indicators.
- Market data layer must **only** fetch data.

## Architecture Layers

```
broker/         → API communication with Alpaca (HTTP, auth, error handling)
market_data/    → fetch symbols and price history (screener, OHLCV bars)
strategy/       → compute indicators and signals (NO broker imports)
execution/      → place and manage orders (NO indicator logic)
orchestration/  → run the daily workflow
config/         → settings via pydantic-settings + .env
logging_config/ → structlog JSON/console setup
scripts/        → entry points (run_morning.py, smoke_test.py)
tests/          → pytest unit tests with mocked httpx (respx)
```

## Development Phases

### PHASE 1 — Project Scaffolding ✅ DONE
- `config/settings.py` — Pydantic `BaseSettings`, all config from `.env`
- `logging_config/setup.py` — `configure_logging()` with structlog
- `.env.example` — all keys documented, no secrets
- `pyproject.toml` — dependencies, ruff/black/pytest config
- `.gitignore`, `Makefile`
- All package `__init__.py` files, `tests/conftest.py`

### PHASE 2 — Broker Layer ✅ DONE
- `broker/exceptions.py` — `BrokerError`, `AuthError`, `OrderRejectedError`, `RateLimitError`, `GatewayError`
- `broker/models.py` — `AccountInfo`, `ScannerRow`, `OHLCVBar`
- `broker/auth.py` — `AlpacaAuth`: validates API key/secret, provides auth headers
- `broker/client.py` — `AlpacaClient`: httpx wrapper, 100ms rate limiting, typed error handling
- `tests/test_auth.py`, `tests/test_client.py` — 13 tests, all passing
- `scripts/smoke_test.py`

### PHASE 3 — Market Data ✅ DONE
- `market_data/models.py` — `ScreenerResult` frozen dataclass (`symbol`, `volume`)
- `market_data/screener.py` — `TopMoversScreener.get_top_movers()` → `list[ScreenerResult]`
- `market_data/history.py` — `HistoricalDataFetcher.fetch_bars()` → `pd.DataFrame` (UTC DatetimeIndex, float64 OHLCV columns)
- `tests/test_screener.py`, `tests/test_history.py` — 18 tests, all passing

### PHASE 4 — Indicators
Create:
- `strategy/signals.py` — pure indicator functions (two groups, see below)
- `strategy/models.py` — `Direction`, `SignalResult` dataclasses
- `tests/test_signals.py` — pure math tests, no mocking needed

No broker imports allowed in this phase.

#### Group 1 — General Momentum Indicators
Standard technical indicators, not tied to any specific strategy:
- `rsi(series, period)` → `pd.Series` — Relative Strength Index
- `ema(series, period)` → `pd.Series` — Exponential Moving Average
- `macd(series, fast, slow, signal)` → `tuple[pd.Series, pd.Series, pd.Series]` — MACD line, signal line, histogram

#### Group 2 — Ross Cameron "First Dip" Indicators
Implements the Gap & Go / First Pullback setup described at:
https://www.youtube.com/watch?v=oxob0x0Xz7s
Entry logic: stock gaps up → initial surge → first pullback to VWAP or 9 EMA → bounce = buy signal.
- `vwap(df)` → `pd.Series` — anchored VWAP from the first bar of the session (pass today's bars only — resets each session)
- `gap_percent(open, prev_close)` → `float` — pre-market gap size: `(open - prev_close) / prev_close`
- `relative_volume(df, lookback_bars)` → `float` — current bar volume vs rolling average (catalyst filter)
- `first_dip_signal(df, ema_period)` → `bool` — detects: gap up → surge above VWAP → first pullback to VWAP or 9 EMA → price reclaims level
- `in_prime_window(ts, tz)` → `bool` — True if bar falls within 9:30–10:30 AM ET (Ross Cameron's prime window)
- `opening_range_breakout(df, range_bars)` → `bool` — True if current bar closes above the high of the first N bars (alternative entry to first dip)

Float filter (separate from signals, lives in `market_data/`):
- `market_data/float_filter.py` — `FloatFetcher`: fetches public float via yfinance; `is_low_float(symbol, max_float=20M)` → `bool`
- Note: Alpaca does not provide float data; yfinance is used as a secondary data source for this one field

### PHASE 5 — Strategy
Create:
- `strategy/base.py` — abstract `Strategy` base class
- `strategy/momentum.py` — `MomentumStrategy(Strategy)`: uses **Group 1** indicators (RSI + MACD + EMA trend filter)
- `strategy/first_dip.py` — `FirstDipStrategy(Strategy)`: uses **Group 2** indicators (Ross Cameron Gap & Go setup)
  - Pre-market gate: `gap_percent > 10%` AND `is_low_float` AND `relative_volume > 2x`
  - Entry: `first_dip_signal` OR `opening_range_breakout`, only within `in_prime_window` (9:30–10:30 AM ET)
  - Requires both `df` (30 days, for relative_volume lookback) and `today_df` (session only, for VWAP/first_dip)
- `tests/test_momentum_strategy.py`
- `tests/test_first_dip_strategy.py`

Output of both strategies: `SignalResult` with direction `BUY` or `NONE`.

### PHASE 6 — Execution
Create:
- `execution/models.py` — `OrderRequest`, `OrderStatus`, `PositionState` dataclasses
- `execution/order_manager.py` — `OrderManager`: size position, place entry, attach TP/SL brackets
- `execution/position_monitor.py` — `PositionMonitor`: poll loop, exit on TP/SL breach
- `tests/test_order_manager.py`, `tests/test_position_monitor.py`

No indicator logic in this layer.

### PHASE 7 — Workflow
Create:
- `orchestration/morning_workflow.py` — `MorningWorkflow.run()`:
  1. Get top movers
  2. Fetch historical data
  3. Compute signals
  4. Place orders
  5. Monitor positions
- `scripts/run_morning.py` — wires all deps via constructor injection, calls `workflow.run()`

## Key Design Decisions
- **Decoupling**: `strategy/` has zero imports from `broker/` or `execution/`
- **Dependency injection**: all collaborators passed via constructor, no globals
- **Paper-trading hard stop**: `OrderManager.__init__` asserts `settings.paper_trading is True`
- **Config via env**: all parameters in `Settings(BaseSettings)`, backed by `.env`
- **Stateless auth**: Alpaca uses API key headers on every request — no session, no keepalive

## Key Alpaca Endpoints

| Purpose | Method | URL |
|---|---|---|
| Validate credentials / account | GET | `{trading_url}/v2/account` |
| Top movers (screener) | GET | `{data_url}/v1beta1/screener/stocks/most-actives` |
| Historical bars | GET | `{data_url}/v2/stocks/{symbol}/bars` |
| Place order | POST | `{trading_url}/v2/orders` |
| Get orders | GET | `{trading_url}/v2/orders` |
| Cancel order | DELETE | `{trading_url}/v2/orders/{order_id}` |
| Portfolio positions | GET | `{trading_url}/v2/positions` |

## Dependencies
| Package | Purpose |
|---|---|
| `httpx` | HTTP client |
| `pydantic>=2.6` | Response model validation |
| `pydantic-settings>=2.2` | `.env`-backed settings |
| `structlog>=24.0` | JSON structured logging |
| `pandas>=2.2` | OHLCV DataFrame + TA |
| `yfinance>=0.2` | Fetch float shares (not available via Alpaca) |
| `respx>=0.21` | Mock httpx in tests |
| `pytest>=8.0` | Test runner |

Note: `pandas-ta` is not available for this Python version. All indicators are implemented in pure pandas/numpy in `strategy/signals.py`.
