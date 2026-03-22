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

### PHASE 3 — Market Data
Create:
- `market_data/screener.py` — `TopMoversScreener.get_top_movers()` using `client.get_top_movers()`
- `market_data/history.py` — `HistoricalDataFetcher.fetch_bars()` returning `pd.DataFrame`
- `market_data/models.py` — `ScreenerResult` dataclass
- `tests/test_screener.py`, `tests/test_history.py`

### PHASE 4 — Indicators
Create:
- `strategy/signals.py` — pure functions: `rsi()`, `ema()`, `macd()`
- `strategy/models.py` — `Direction`, `SignalResult` dataclasses
- `tests/test_signals.py` — pure math tests, no mocking needed

No broker imports allowed in this phase.

### PHASE 5 — Strategy
Create:
- `strategy/momentum.py` — `MomentumStrategy(Strategy)`: RSI + MACD + EMA trend filter
- `strategy/base.py` — abstract `Strategy` base class
- `tests/test_momentum_strategy.py`

Output: `SignalResult` with direction `BUY` or `NONE`.

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
| `respx>=0.21` | Mock httpx in tests |
| `pytest>=8.0` | Test runner |

Note: `pandas-ta` is not available for this Python version. All indicators are implemented in pure pandas/numpy in `strategy/signals.py`.
