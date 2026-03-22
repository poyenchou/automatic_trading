"""
Smoke test — verify the broker and market data layers work against the live Alpaca API.

PREREQUISITES
─────────────
1. Sign up for a free Alpaca paper trading account:
       https://alpaca.markets → click "Start Paper Trading"

2. Get your API keys:
       Dashboard → Paper Trading → API Keys → Generate New Key
       Copy the Key ID and Secret Key (secret is only shown once).

3. Copy and fill in your .env:
       cp .env.example .env
       # Set: ALPACA_API_KEY=<your key id>
       #      ALPACA_API_SECRET=<your secret key>

USAGE
─────
    python scripts/smoke_test.py
"""

import sys
from datetime import datetime, timedelta, timezone

import mplfinance as mpf

sys.path.insert(0, ".")

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, BrokerError
from config.settings import Settings
from logging_config.setup import configure_logging
from market_data.history import HistoricalDataFetcher
from market_data.screener import TopMoversScreener


def ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def fail(msg: str) -> None:
    print(f"  ✗  {msg}")


def section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def main() -> None:
    settings = Settings()
    configure_logging(level="WARNING", fmt="console")  # suppress debug noise

    print("\nAlpaca Trading Bot — Smoke Test")
    print(f"Trading URL: {settings.alpaca_trading_url}")

    # ── 1. Credentials ───────────────────────────────────────────────────────
    section("1 / Credentials")
    auth = AlpacaAuth(settings)
    try:
        auth.validate_credentials()
        ok("API credentials are valid")
    except AuthError as exc:
        fail(str(exc))
        print("\n  → Set ALPACA_API_KEY and ALPACA_API_SECRET in your .env file.")
        sys.exit(1)

    client = AlpacaClient(settings=settings, auth=auth)

    try:
        account = client.get_account()
        ok(f"Account {account.id} — status: {account.status}")
    except BrokerError as exc:
        fail(f"get_account failed: {exc}")

    # ── 2. Screener (market data layer) ──────────────────────────────────────
    section("2 / Screener  [market_data.screener]")
    first_symbol: str = ""
    screener = TopMoversScreener(client=client, settings=settings)
    try:
        results = screener.get_top_movers()
        ok(f"Received {len(results)} ScreenerResults")
        for r in results:
            print(f"       {r.symbol:<6}  volume={r.volume:,.0f}")
        if results:
            first_symbol = results[0].symbol
    except BrokerError as exc:
        fail(f"get_top_movers failed: {exc}")

    # ── 3. Historical bars (market data layer) ────────────────────────────────
    section("3 / Historical bars  [market_data.history]")
    df = None
    fetcher = HistoricalDataFetcher(client=client)
    if not first_symbol:
        print("  (skipped — no symbol from screener)")
    else:
        try:
            # Go back 7 days to guarantee we cover at least 5 trading days,
            # so the chart works on weekends when today has no data yet.
            start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            df = fetcher.fetch_bars(first_symbol, timeframe="5Min", start=start, limit=50)
            ok(f"{first_symbol} — {len(df)} bars  (5Min, last 7 days)")
            ok(f"DataFrame schema: index={df.index.dtype}, columns={list(df.columns)}")
            print(df.tail())
        except (BrokerError, ValueError) as exc:
            fail(f"fetch_bars failed: {exc}")

    # ── 4. Chart ──────────────────────────────────────────────────────────────
    section("4 / Chart")
    if df is None or df.empty:
        print("  (skipped — no bars to plot)")
    else:
        # mplfinance requires capitalised column names
        plot_df = df.rename(columns=str.capitalize)
        print(plot_df.head())
        ok(f"Opening candlestick chart for {first_symbol}…")
        mpf.plot(plot_df, type="candle", volume=True, title=f"{first_symbol} — 5Min (last 7 days)", style="charles")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print("  Done.\n")
    client.close()


if __name__ == "__main__":
    main()
