"""
dry_run.py — validates the full workflow without placing any orders.

Runs every layer (screener → float filter → history → price filter → signals →
position sizing) and prints what would happen if this were a live run.
No orders are placed. Safe to run anytime.

Usage:
    python scripts/dry_run.py
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
from market_data.screener import TopMoversScreener
from strategy.first_dip import FirstDipStrategy
from strategy.momentum import MomentumStrategy
from strategy.models import Direction

ET = ZoneInfo("America/New_York")


def _market_hours_warning() -> None:
    """Print a warning if running outside market hours — dry run still proceeds."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        day = "Saturday" if now.weekday() == 5 else "Sunday"
        print(f"\n  ⚠  It's {day} — market is closed. Signals will use last Friday's data.")
        print(f"     This is a dry run only, so no orders would be placed anyway.\n")
    elif not (9 <= now.hour < 11):
        print(f"\n  ⚠  Current time {now.strftime('%H:%M %Z')} is outside 9:00–11:00 AM ET.")
        print(f"     FirstDipStrategy will return NONE (prime window gate).\n")


def ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def skip(msg: str) -> None:
    print(f"  –  {msg}")


def section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def main() -> None:
    settings = Settings()
    configure_logging(level="WARNING", fmt="console")

    _market_hours_warning()
    print("\nAlpaca Trading Bot — Dry Run")
    print(f"Min stock price:          ${settings.min_stock_price:.2f}")
    print(f"Max concurrent positions: {settings.max_concurrent_positions}")
    print(f"Risk per trade:           {settings.risk_per_trade_pct * 100:.1f}% of equity")
    print(f"Stop loss:                ${settings.stop_loss_cents:.2f} below entry")
    print(f"Take profit:              2:1 R/R (${settings.stop_loss_cents * 2:.2f} above entry)")

    # ── Credentials ───────────────────────────────────────────────────────────
    section("1 / Credentials")
    auth = AlpacaAuth(settings)
    try:
        auth.validate_credentials()
        ok("API credentials valid")
    except AuthError as exc:
        print(f"  ✗  {exc}")
        sys.exit(1)

    client = AlpacaClient(settings=settings, auth=auth)
    account = client.get_account()
    ok(f"Account equity: ${account.equity:,.2f}")

    # ── Screener ──────────────────────────────────────────────────────────────
    section("2 / Screener")
    screener = TopMoversScreener(client=client, settings=settings)
    movers = screener.get_top_movers()
    ok(f"Top {len(movers)} movers: {[m.symbol for m in movers]}")

    # ── Per-symbol pipeline ───────────────────────────────────────────────────
    fetcher       = HistoricalDataFetcher(client=client)
    float_fetcher = FloatFetcher()
    strategies    = [
        FirstDipStrategy(float_fetcher=None, min_gap_pct=0.10, min_rel_vol=2.0),
        MomentumStrategy(rsi_oversold=settings.rsi_oversold),
    ]

    buy_count = 0

    for mover in movers:
        symbol = mover.symbol
        section(f"Symbol: {symbol}")

        # ── Price filter ──────────────────────────────────────────────────────
        # Fetch 1 day of bars to get the current price cheaply before pulling
        # the full 30-day history. Alpaca free tier requires a start date.
        try:
            price_start = (datetime.now(timezone.utc) - timedelta(days=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            bars = fetcher.fetch_bars(symbol, timeframe="5Min", start=price_start, limit=10)
            if bars.empty:
                skip("no bar data available")
                continue
            last_price = float(bars["close"].iloc[-1])
        except BrokerError as exc:
            skip(f"history fetch failed: {exc}")
            continue

        if last_price < settings.min_stock_price:
            skip(f"price ${last_price:.2f} below minimum ${settings.min_stock_price:.2f} — SKIP")
            continue
        ok(f"price ${last_price:.2f} passes minimum filter")

        # ── Float filter ──────────────────────────────────────────────────────
        float_shares = float_fetcher.get_float_shares(symbol)
        if float_shares is not None:
            ok(f"float: {float_shares:,} shares ({'low ✓' if float_shares <= 20_000_000 else 'high — would skip'})")
        else:
            skip("float data unavailable (would skip in live run)")

        # ── History ───────────────────────────────────────────────────────────
        try:
            start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            df = fetcher.fetch_bars(symbol, timeframe="5Min", start=start, limit=2000)
            df.index = df.index.tz_convert(ET)
            df = df.between_time("09:30", "15:55")
            df.index = df.index.tz_convert("UTC")
            idx_et   = df.index.tz_convert(ET)
            last_date = idx_et.date.max()
            today_df  = df[idx_et.date == last_date]
            ok(f"history: {len(df)} bars total, {len(today_df)} bars today ({last_date})")
        except BrokerError as exc:
            skip(f"history fetch failed: {exc}")
            continue

        # ── Signals ───────────────────────────────────────────────────────────
        for strategy in strategies:
            result = strategy.generate_signal(symbol, df, today_df)
            arrow  = "→ BUY  " if result.direction == Direction.BUY else "→ NONE "
            print(f"       {strategy.__class__.__name__:<20} {arrow}  {result.reason}")

            if result.direction == Direction.BUY:
                buy_count += 1
                # ── Position sizing (no order placed) ─────────────────────────
                risk_dollars = account.equity * settings.risk_per_trade_pct
                qty          = max(1, min(int(risk_dollars / settings.stop_loss_cents),
                                         settings.max_shares))
                current_price = float(df["close"].iloc[-1])
                stop_price    = round(current_price - settings.stop_loss_cents, 2)
                tp_price      = round(current_price + 2 * settings.stop_loss_cents, 2)
                print(f"       {'':20} qty={qty}, entry≈{current_price:.4f}, "
                      f"stop={stop_price:.4f}, tp={tp_price:.4f}  [DRY RUN — no order placed]")
                break  # first BUY wins per symbol

        if buy_count >= settings.max_concurrent_positions:
            print(f"\n  Max positions ({settings.max_concurrent_positions}) reached — stopping scan.")
            break

    # ── Summary ───────────────────────────────────────────────────────────────
    section("Summary")
    if buy_count == 0:
        print("  No BUY signals fired. Bot would exit without placing orders.")
        print("  This is normal outside 9:30–10:30 AM ET or on low-activity days.")
    else:
        ok(f"{buy_count} BUY signal(s) would result in orders on a live run.")
    print()

    client.close()


if __name__ == "__main__":
    main()
