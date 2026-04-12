"""
run_morning.py — entry point for the daily morning trading session.

Usage:
    python scripts/run_morning.py

Wires all dependencies via constructor injection and runs MorningWorkflow.
Safe to run: will refuse to execute if PAPER_TRADING is not True in .env.
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, BrokerError
from config.settings import Settings
from logging_config.setup import configure_logging
from market_data.float_filter import FloatFetcher
from orchestration.morning_workflow import MorningWorkflow
from strategy.first_dip import FirstDipStrategy
from strategy.momentum import MomentumStrategy


def _check_market_hours() -> None:
    """Exit early if it's a weekend or outside 9:00–11:00 AM ET."""
    ET = ZoneInfo("America/New_York")
    now = datetime.now(ET)
    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        print(f"Today is {'Saturday' if now.weekday() == 5 else 'Sunday'} — market is closed. Exiting.")
        sys.exit(0)
    if not (9 <= now.hour < 11):
        print(f"Current time is {now.strftime('%H:%M %Z')} — outside trading window (9:00–11:00 AM ET). Exiting.")
        sys.exit(0)


def main() -> None:
    _check_market_hours()

    settings = Settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    # ── Credentials ───────────────────────────────────────────────────────────
    auth = AlpacaAuth(settings)
    try:
        auth.validate_credentials()
    except AuthError as exc:
        print(f"Authentication failed: {exc}")
        print("Set ALPACA_API_KEY and ALPACA_API_SECRET in your .env file.")
        sys.exit(1)

    client = AlpacaClient(settings=settings, auth=auth)

    # ── Dependency injection ──────────────────────────────────────────────────
    float_fetcher = FloatFetcher()

    # Active strategy: FirstDipStrategy (Ross Cameron Gap & Go)
    # To switch to general momentum, comment out the first block and
    # uncomment the second.
    strategies = [
        FirstDipStrategy(
            float_fetcher=float_fetcher,
            
            min_rel_vol=2.0,
        ),
    ]

    # strategies = [
    #     MomentumStrategy(
    #         rsi_oversold=settings.rsi_oversold,
    #     ),
    # ]

    workflow = MorningWorkflow(
        client=client,
        settings=settings,
        strategies=strategies,
        float_fetcher=float_fetcher,
    )

    # ── Run ───────────────────────────────────────────────────────────────────
    print("\nStarting morning workflow...")
    print(f"Max concurrent positions: {settings.max_concurrent_positions}")
    print(f"Risk per trade:           {settings.risk_per_trade_pct * 100:.1f}% of equity")
    print(f"Stop loss:                ${settings.stop_loss_cents:.2f} below entry")
    print(f"Take profit:              2:1 R/R (${settings.stop_loss_cents * 2:.2f} above entry)")
    print(f"Poll interval:            {settings.poll_interval_seconds}s\n")

    try:
        results = workflow.run()
    except BrokerError as exc:
        print(f"Broker error: {exc}")
        sys.exit(1)
    finally:
        client.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Session Summary ──────────────────────────────────────────────")
    for r in results:
        if r.outcome == "skipped":
            print(f"  SKIP  {r.symbol:<6}  {r.reason}")
        elif r.outcome == "tp":
            print(f"  WIN   {r.symbol:<6}  take-profit hit")
        elif r.outcome == "sl":
            print(f"  LOSS  {r.symbol:<6}  stop-loss hit")
        elif r.outcome == "manual":
            print(f"  EXIT  {r.symbol:<6}  closed manually (bracket orders missing)")
    print("─────────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
