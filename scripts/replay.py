"""
replay.py — replay a historical session bar-by-bar to verify strategy behaviour.

Simulates the morning scan loop for a specific past date by feeding
today_df incrementally (one bar at a time) to the strategy, exactly
as it would receive data during a live session. No orders are placed.

Use this on weekends to verify that:
  - The strategy fires at the correct bar
  - Relative volume is computed correctly from cross-day history
  - Stop/TP prices would be placed at sensible levels

Usage:
    python scripts/replay.py --date 2026-04-10 --symbol SQFT
    python scripts/replay.py --date 2026-04-10 --symbol SQFT,OGN --all-bars

Options:
    --date      YYYY-MM-DD  Session date to replay (required)
    --symbol    SYM,...     Comma-separated list of symbols (required)
    --all-bars              Show every bar, not just bars with changed signal
                            (default: only print when signal or reason changes)
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, BrokerError
from config.settings import Settings
from logging_config.setup import configure_logging
from market_data.history import HistoricalDataFetcher
from strategy.first_dip import FirstDipStrategy
from strategy.momentum import MomentumStrategy
from strategy.models import Direction

ET = ZoneInfo("America/New_York")

# Prime window for FirstDipStrategy
PRIME_OPEN  = "09:30"
PRIME_CLOSE = "10:30"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a historical session bar-by-bar to verify strategy signals."
    )
    parser.add_argument(
        "--date", required=True, metavar="YYYY-MM-DD",
        help="Session date to replay",
    )
    parser.add_argument(
        "--symbol", required=True, metavar="SYM[,SYM...]",
        help="Comma-separated list of symbols",
    )
    parser.add_argument(
        "--all-bars", action="store_true",
        help="Print every bar, not just bars where signal/reason changes",
    )
    return parser.parse_args()


def _replay_symbol(
    symbol: str,
    replay_date: str,
    fetcher: HistoricalDataFetcher,
    strategies: list,
    settings: Settings,
    all_bars: bool,
) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {symbol}  —  {replay_date}")
    print(f"{'─' * 60}")

    # Fetch 14 days ending on replay_date (inclusive)
    end_dt = datetime.strptime(replay_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Add 1 day so end is exclusive-end of replay_date (gets full day's bars)
    end_str   = (end_dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_str = (end_dt - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        df = fetcher.fetch_bars(symbol, timeframe="5Min", start=start_str, end=end_str, limit=2000)
    except BrokerError as exc:
        print(f"  ✗  Failed to fetch bars: {exc}")
        return

    if df.empty:
        print(f"  –  No bars returned for {symbol} ending {replay_date}")
        return

    # Filter to regular market hours
    df.index = df.index.tz_convert(ET)
    df = df.between_time("09:30", "15:55")
    df.index = df.index.tz_convert("UTC")

    # Split into prior history and replay session
    idx_et      = df.index.tz_convert(ET)
    replay_date_obj = datetime.strptime(replay_date, "%Y-%m-%d").date()
    prior_df    = df[idx_et.date < replay_date_obj]
    today_df    = df[idx_et.date == replay_date_obj]

    if today_df.empty:
        print(f"  –  No bars found for {replay_date} (market may have been closed)")
        return

    # Restrict replay to prime window bars only
    today_et  = today_df.index.tz_convert(ET)
    prime_df  = today_df[today_et.time <= datetime.strptime(PRIME_CLOSE, "%H:%M").time()]

    prior_dates = len(set(prior_df.index.tz_convert(ET).date))
    print(f"  Prior history : {len(prior_df)} bars across {prior_dates} sessions")
    print(f"  Session bars  : {len(today_df)} total, {len(prime_df)} within prime window (9:30–10:30)")

    if prime_df.empty:
        print(f"  –  No prime window bars on {replay_date}")
        return

    # Show session open and gap vs prior close
    open_price = float(today_df["open"].iloc[0])
    if not prior_df.empty:
        prev_close  = float(prior_df["close"].iloc[-1])
        gap_pct     = (open_price - prev_close) / prev_close * 100
        print(f"  Prev close    : ${prev_close:.2f}")
        print(f"  Session open  : ${open_price:.2f}  (gap {gap_pct:+.1f}%)")
    else:
        print(f"  Session open  : ${open_price:.2f}  (no prior close available)")

    print()
    print(f"  {'Time (ET)':<10}  {'Close':>7}  {'Vol':>10}  {'Signal':<6}  Reason")
    print(f"  {'─'*10}  {'─'*7}  {'─'*10}  {'─'*6}  {'─'*40}")

    last_reason = {}   # strategy name → last printed reason
    buy_fired   = False

    # Replay bar by bar
    for i in range(1, len(prime_df) + 1):
        incremental_today = today_df.iloc[:i]  # today up to bar i (within full today_df)
        # Only include prime-window bars
        inc_et = incremental_today.index.tz_convert(ET)
        incremental_today = incremental_today[
            inc_et.time <= datetime.strptime(PRIME_CLOSE, "%H:%M").time()
        ]
        if incremental_today.empty:
            continue

        # Full df for this bar = prior history + today so far
        df_so_far = df[df.index <= incremental_today.index[-1]]

        bar     = incremental_today.iloc[-1]
        bar_et  = incremental_today.index[-1].tz_convert(ET)
        time_str = bar_et.strftime("%H:%M")
        close   = bar["close"]
        vol     = int(bar["volume"])

        for strategy in strategies:
            result = strategy.generate_signal(symbol, df_so_far, incremental_today)
            sname  = strategy.__class__.__name__

            is_buy     = result.direction == Direction.BUY
            changed    = result.reason != last_reason.get(sname)

            if all_bars or changed or is_buy:
                direction_str = "BUY  " if is_buy else "NONE "
                print(f"  {time_str:<10}  ${close:>6.2f}  {vol:>10,}  {direction_str}  {result.reason}")
                last_reason[sname] = result.reason

            if is_buy and not buy_fired:
                buy_fired = True
                stop  = round(close - settings.stop_loss_cents, 2)
                tp    = round(close + 2 * settings.stop_loss_cents, 2)
                print()
                print(f"  *** BUY SIGNAL at {time_str} ***")
                print(f"      Entry ≈  ${close:.2f}  (market order — actual fill may differ)")
                print(f"      Stop     ${stop:.2f}  (-${settings.stop_loss_cents:.2f})")
                print(f"      Target   ${tp:.2f}  (+${settings.stop_loss_cents * 2:.2f}, 2:1 R/R)")
                print()

    if not buy_fired:
        print()
        print(f"  No BUY signal fired during prime window on {replay_date}.")


def main() -> None:
    args    = _parse_args()
    symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    settings = Settings()
    configure_logging(level="WARNING", fmt="console")

    # Auth
    auth = AlpacaAuth(settings)
    try:
        auth.validate_credentials()
    except AuthError as exc:
        print(f"Authentication failed: {exc}")
        sys.exit(1)

    client  = AlpacaClient(settings=settings, auth=auth)
    fetcher = HistoricalDataFetcher(client=client)

    float_fetcher = None   # skip float filter in replay — focus on signal logic

    strategies = [
        FirstDipStrategy(
            float_fetcher=float_fetcher,
            min_rel_vol=settings.first_dip_min_rel_vol,
            max_float=settings.first_dip_max_float,
            ema_period=settings.first_dip_ema_period,
            range_bars=settings.first_dip_range_bars,
        ),
    ]

    print(f"\nReplay — {args.date}")
    print(f"Symbols : {', '.join(symbols)}")
    print(f"Strategy: FirstDipStrategy  (float filter disabled)")
    print(f"Settings: min_rel_vol={settings.first_dip_min_rel_vol}x  "
          f"stop=${settings.stop_loss_cents:.2f}  tp=${settings.stop_loss_cents * 2:.2f}")

    for symbol in symbols:
        _replay_symbol(
            symbol=symbol,
            replay_date=args.date,
            fetcher=fetcher,
            strategies=strategies,
            settings=settings,
            all_bars=args.all_bars,
        )

    print()
    client.close()


if __name__ == "__main__":
    main()
