# Automatic Trading Bot — Claude Context

## Project Overview
Python automated trading bot using the Interactive Brokers Client Portal Web API (REST-style, NOT TWS socket API). The gateway runs locally at `https://localhost:5000/v1/api`. Development is local first, then EC2.

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
broker/         → API communication with IBKR (HTTP, auth, retry, error handling)
market_data/    → fetch symbols and price history (screener, OHLCV bars)
strategy/       → compute indicators and signals (NO broker imports)
execution/      → place and manage orders (NO indicator logic)
orchestration/  → run the daily workflow
config/         → settings via pydantic-settings + .env
logging_config/ → structlog JSON/console setup
scripts/        → entry points (run_morning.py, keepalive_only.py)
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
- `broker/models.py` — Pydantic models for all IBKR responses
- `broker/auth.py` — `SessionManager`: auth check, tickle keepalive (5-min daemon thread), reauthenticate
- `broker/client.py` — `IBKRClient`: httpx wrapper, token-bucket rate limiter (10 req/s), tenacity retry (3× on 5xx/network errors)
- `tests/test_auth.py`, `tests/test_client.py` — 29 tests, all passing
- `scripts/keepalive_only.py`

### PHASE 3 — Market Data
Create:
- `market_data/screener.py` — `TopMoversScreener.get_top_movers()` using `/iserver/scanner/run`
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
- **Auth keepalive**: daemon thread calls `POST /tickle` every 5 min (3× safety margin on 15-min timeout)
- **SSL**: `verify=False` throughout (gateway self-signed cert, traffic stays on localhost)

## Key IBKR Endpoints

| Purpose | Method | Path |
|---|---|---|
| Auth status | GET | `/iserver/auth/status` |
| Keepalive | POST | `/tickle` |
| Reauthenticate | POST | `/iserver/reauthenticate` |
| Scanner / movers | POST | `/iserver/scanner/run` |
| Market data snapshot | GET | `/iserver/marketdata/snapshot` |
| Historical bars | GET | `/iserver/marketdata/history` |
| Place order | POST | `/iserver/account/{id}/orders` |
| Get orders | GET | `/iserver/account/{id}/orders` |
| Cancel order | DELETE | `/iserver/account/{id}/order/{orderId}` |
| Portfolio positions | GET | `/portfolio/{id}/positions/0` |

## Dependencies
| Package | Purpose |
|---|---|
| `httpx` | HTTP client |
| `pydantic>=2.6` | Response model validation |
| `pydantic-settings>=2.2` | `.env`-backed settings |
| `structlog>=24.0` | JSON structured logging |
| `pandas>=2.2` | OHLCV DataFrame + TA |
| `tenacity>=8.2` | Retry with exponential backoff |
| `respx>=0.21` | Mock httpx in tests |
| `pytest>=8.0` | Test runner |

Note: `pandas-ta` is not available for this Python version. All indicators are implemented in pure pandas/numpy in `strategy/signals.py`.
