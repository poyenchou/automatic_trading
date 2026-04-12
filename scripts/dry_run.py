"""
dry_run.py — pre-flight checks + full pipeline simulation.

Run this before market open to confirm everything is wired up, then see
what the bot would actually do if this were a live run.

Usage:
    python scripts/dry_run.py

Section 1 — Pre-flight: tests each external dependency individually.
Section 2 — Pipeline:   runs screener → float filter → history → signals
                        and prints what orders would be placed (no orders placed).
"""

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, BrokerError
from config.settings import Settings
from logging_config.setup import configure_logging
from market_data.float_filter import FloatFetcher
from market_data.history import HistoricalDataFetcher
from market_data.screener import GapScreener
from strategy.first_dip import FirstDipStrategy
from strategy.momentum import MomentumStrategy
from strategy.models import Direction

ET = ZoneInfo("America/New_York")

PASS = "  ✓"
FAIL = "  ✗"
SKIP = "  –"


def section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def _market_hours_warning() -> None:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        day = "Saturday" if now.weekday() == 5 else "Sunday"
        print(f"\n  ⚠  It's {day} — market closed. Signals use last available data.")
        print(f"     No orders would be placed anyway.\n")
    elif not (9 <= now.hour < 11):
        print(f"\n  ⚠  {now.strftime('%H:%M %Z')} is outside 9:00–11:00 AM ET.")
        print(f"     FirstDipStrategy will return NONE (prime window gate).\n")


# ── Section 1: Pre-flight ─────────────────────────────────────────────────────

def preflight(settings: Settings) -> AlpacaClient | None:
    section("1 / Pre-flight checks")

    # 1a. Auth
    auth = AlpacaAuth(settings)
    try:
        auth.validate_credentials()
        print(f"{PASS}  Auth — API credentials valid")
    except AuthError as exc:
        print(f"{FAIL}  Auth — {exc}")
        print("       Set ALPACA_API_KEY and ALPACA_API_SECRET in your .env file.")
        return None

    client = AlpacaClient(settings=settings, auth=auth)

    # 1b. Account
    try:
        account = client.get_account()
        print(f"{PASS}  Account — equity=${account.equity:,.2f}  buying_power=${account.buying_power:,.2f}")
    except BrokerError as exc:
        print(f"{FAIL}  Account — {exc}")
        return None

    # 1c. Screener
    try:
        screener = GapScreener(client=client, settings=settings)
        movers = screener.get_gappers()
        symbols = [m.symbol for m in movers]
        print(f"{PASS}  Screener — {len(movers)} gappers: {symbols}")
    except BrokerError as exc:
        print(f"{FAIL}  Screener — {exc}")
        return None

    # 1d. Historical bars (spot-check first mover)
    if movers:
        symbol = movers[0].symbol
        try:
            fetcher = HistoricalDataFetcher(client=client)
            start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            bars = fetcher.fetch_bars(symbol, timeframe="5Min", start=start, limit=10)
            if bars.empty:
                print(f"{SKIP}  Bars ({symbol}) — no data returned")
            else:
                last = bars.iloc[-1]
                print(f"{PASS}  Bars ({symbol}) — {len(bars)} bars, last close=${float(last['close']):.2f}")
        except BrokerError as exc:
            print(f"{FAIL}  Bars ({symbol}) — {exc}")

    # 1e. Float data (yfinance)
    if movers:
        symbol = movers[0].symbol
        try:
            float_fetcher = FloatFetcher()
            shares = float_fetcher.get_float_shares(symbol)
            if shares is not None:
                print(f"{PASS}  Float ({symbol}) — {shares:,} shares")
            else:
                print(f"{SKIP}  Float ({symbol}) — data unavailable (yfinance)")
        except Exception as exc:
            print(f"{FAIL}  Float ({symbol}) — {exc}")

    # 1f. Positions (confirms position query works)
    try:
        pos = client.get_position("AAPL")   # expect None when flat
        print(f"{PASS}  Positions — query works (AAPL: {'open' if pos else 'flat'})")
    except BrokerError as exc:
        print(f"{FAIL}  Positions — {exc}")

    return client


# ── Section 2: Pipeline simulation ───────────────────────────────────────────

def pipeline(client: AlpacaClient, settings: Settings) -> None:
    section("2 / Pipeline simulation  [DRY RUN — no orders placed]")

    account  = client.get_account()
    screener = GapScreener(client=client, settings=settings)
    movers   = screener.get_gappers()

    fetcher       = HistoricalDataFetcher(client=client)
    float_fetcher = FloatFetcher()
    strategies    = [
        FirstDipStrategy(float_fetcher=None,  min_rel_vol=2.0),
        MomentumStrategy(rsi_oversold=settings.rsi_oversold),
    ]

    buy_count = 0

    for mover in movers:
        symbol = mover.symbol
        section(f"Symbol: {symbol}")

        # Price filter
        try:
            start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            bars = fetcher.fetch_bars(symbol, timeframe="5Min", start=start, limit=10)
            if bars.empty:
                print(f"{SKIP}  no bar data")
                continue
            last_price = float(bars["close"].iloc[-1])
        except BrokerError as exc:
            print(f"{SKIP}  history fetch failed: {exc}")
            continue

        if last_price < settings.min_stock_price:
            print(f"{SKIP}  price ${last_price:.2f} below minimum ${settings.min_stock_price:.2f}")
            continue
        print(f"{PASS}  price ${last_price:.2f}")

        # Float filter
        float_shares = float_fetcher.get_float_shares(symbol)
        if float_shares is not None:
            label = "low ✓" if float_shares <= 20_000_000 else "high — would skip"
            print(f"{PASS}  float: {float_shares:,} shares ({label})")
        else:
            print(f"{SKIP}  float unavailable")

        # Full history for signals
        try:
            start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            df = fetcher.fetch_bars(symbol, timeframe="5Min", start=start, limit=2000)
            df.index = df.index.tz_convert(ET)
            df = df.between_time("09:30", "15:55")
            df.index = df.index.tz_convert("UTC")
            idx_et    = df.index.tz_convert(ET)
            last_date = idx_et.date.max()
            today_df  = df[idx_et.date == last_date]
            print(f"{PASS}  history: {len(df)} bars total, {len(today_df)} bars today ({last_date})")
        except BrokerError as exc:
            print(f"{SKIP}  history fetch failed: {exc}")
            continue

        # Signals
        for strategy in strategies:
            result = strategy.generate_signal(symbol, df, today_df)
            arrow  = "→ BUY " if result.direction == Direction.BUY else "→ NONE"
            print(f"       {strategy.__class__.__name__:<22} {arrow}  {result.reason}")

            if result.direction == Direction.BUY:
                buy_count += 1
                risk_dollars  = account.equity * settings.risk_per_trade_pct
                qty           = max(1, min(int(risk_dollars / settings.stop_loss_cents), settings.max_shares))
                stop_price    = round(last_price - settings.stop_loss_cents, 2)
                tp_price      = round(last_price + 2 * settings.stop_loss_cents, 2)
                print(f"       {'':22} qty={qty}, entry≈{last_price:.4f}, "
                      f"stop={stop_price:.4f}, tp={tp_price:.4f}")
                break

        if buy_count >= settings.max_concurrent_positions:
            print(f"\n  Max positions ({settings.max_concurrent_positions}) reached — stopping scan.")
            break

    section("Summary")
    if buy_count == 0:
        print("  No BUY signals. Bot would exit without placing orders.")
        print("  Normal outside 9:30–10:30 AM ET or on low-activity days.")
    else:
        print(f"{PASS}  {buy_count} BUY signal(s) — orders would be placed on a live run.")
    print()

    client.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    settings = Settings()
    configure_logging(level="WARNING", fmt="console")

    _market_hours_warning()
    print("\nAlpaca Trading Bot — Pre-flight + Dry Run")
    print(f"  Min price:      ${settings.min_stock_price:.2f}")
    print(f"  Max positions:  {settings.max_concurrent_positions}")
    print(f"  Risk per trade: {settings.risk_per_trade_pct * 100:.1f}% of equity")
    print(f"  Stop loss:      ${settings.stop_loss_cents:.2f}  |  Take profit: ${settings.stop_loss_cents * 2:.2f}")

    client = preflight(settings)
    if client is None:
        print("\nPre-flight failed — fix the errors above before running the bot.")
        sys.exit(1)

    pipeline(client, settings)


if __name__ == "__main__":
    main()
