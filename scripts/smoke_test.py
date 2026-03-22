"""
Smoke test — verify the broker layer works against the live Alpaca API.

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

sys.path.insert(0, ".")

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, BrokerError
from config.settings import Settings
from logging_config.setup import configure_logging


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

    print("\nAlpaca Broker Layer — Smoke Test")
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

    # ── 2. Top movers ─────────────────────────────────────────────────────────
    section("2 / Top movers")
    first_symbol: str = ""
    try:
        rows = client.get_top_movers(top=5)
        ok(f"Received {len(rows)} rows")
        for r in rows:
            print(f"       {r.symbol:<6}  pct={r.pct_change:+.2f}%  price={r.price}")
        if rows:
            first_symbol = rows[0].symbol
    except BrokerError as exc:
        fail(f"get_top_movers failed: {exc}")

    # ── 3. Historical bars ────────────────────────────────────────────────────
    section("3 / Historical bars")
    if not first_symbol:
        print("  (skipped — no symbol from top movers)")
    else:
        try:
            bars = client.get_historical_bars(first_symbol, timeframe="5Min", limit=50)
            ok(f"{first_symbol} — {len(bars)} bars (5Min)")
            if bars:
                b = bars[-1]
                print(f"       last bar  o={b.open}  h={b.high}  l={b.low}  c={b.close}  v={b.volume}")
        except BrokerError as exc:
            fail(f"get_historical_bars failed: {exc}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print("  Done.\n")
    client.close()


if __name__ == "__main__":
    main()
