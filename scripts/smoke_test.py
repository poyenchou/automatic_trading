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
import pandas as pd

sys.path.insert(0, ".")

from broker.auth import AlpacaAuth
from broker.client import AlpacaClient
from broker.exceptions import AuthError, BrokerError
from config.settings import Settings
from logging_config.setup import configure_logging
from market_data.history import HistoricalDataFetcher
from market_data.screener import GapScreener
from strategy.signals import ema, first_dip_signal, gap_percent, macd, relative_volume, rsi, vwap


def ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def fail(msg: str) -> None:
    print(f"  ✗  {msg}")


def section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def main() -> None:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

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
    screener = GapScreener(client=client, settings=settings)
    try:
        results = screener.get_gappers()
        ok(f"Received {len(results)} gappers")
        for r in results:
            print(f"       {r.symbol:<6}  gap={r.gap_pct * 100:.1f}%  volume={r.volume:,.0f}")
        if results:
            first_symbol = results[0].symbol
    except BrokerError as exc:
        fail(f"get_gappers failed: {exc}")

    # ── 3. Historical bars (market data layer) ────────────────────────────────
    section("3 / Historical bars  [market_data.history]")
    df = None
    fetcher = HistoricalDataFetcher(client=client)
    if not first_symbol:
        print("  (skipped — no symbol from screener)")
    else:
        try:
            # Fetch 30 days of 5-min bars (~22 trading days × 78 bars ≈ 1700 bars).
            # Alpaca free tier requires a start date; limit=2000 ensures we reach today.
            # RSI(14) needs ~200+ bars to converge to Yahoo Finance / TradingView values.
            start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            df = fetcher.fetch_bars(first_symbol, timeframe="5Min", start=start, limit=2000)
            # Alpaca returns extended-hours bars by default. Keep regular market
            # hours only (9:30 AM – 4:00 PM ET) so indicators match charting sites.
            df.index = df.index.tz_convert(ET)
            df = df.between_time("09:30", "15:55")
            df.index = df.index.tz_convert("UTC")
            ok(f"{first_symbol} — {len(df)} bars  (5Min, regular hours, last 30 days)")
            ok(f"DataFrame schema: index={df.index.dtype}, columns={list(df.columns)}")
            print(df.tail().copy().set_axis(df.tail().index.tz_convert(ET), axis=0))
        except (BrokerError, ValueError) as exc:
            fail(f"fetch_bars failed: {exc}")

    # ── 4. Indicators (strategy layer) ───────────────────────────────────────
    section("4 / Indicators  [strategy.signals]")
    if df is None or df.empty:
        print("  (skipped — no bars available)")
    else:
        # ── Group 1: General Momentum ────────────────────────────────────────
        print("\n  Group 1 — General Momentum Indicators")
        print(f"  Verify on TradingView: open {first_symbol} → 5Min chart → add RSI(14), EMA(9), MACD(12,26,9)")
        print(f"  Note: values may differ slightly if TradingView uses more warm-up bars than we fetched.\n")

        rsi_series   = rsi(df["close"], period=14)
        ema9_series  = ema(df["close"], period=9)
        ema20_series = ema(df["close"], period=20)
        macd_line, signal_line, histogram = macd(df["close"])

        # Print the last 5 bars with indicator values
        indicator_df = df[["close"]].copy()
        indicator_df["RSI(14)"]        = rsi_series.round(2)
        indicator_df["EMA(9)"]         = ema9_series.round(4)
        indicator_df["EMA(20)"]        = ema20_series.round(4)
        indicator_df["MACD"]           = macd_line.round(4)
        indicator_df["MACD_signal"]    = signal_line.round(4)
        indicator_df["MACD_hist"]      = histogram.round(4)
        display = indicator_df.tail(5).copy()
        display.index = display.index.tz_convert(ET)
        print(display.to_string())

        latest_rsi = rsi_series.dropna().iloc[-1] if not rsi_series.dropna().empty else float("nan")
        ok(f"RSI(14) last value: {latest_rsi:.2f}  (overbought >70, oversold <30)")
        ok(f"EMA(9)  last value: {ema9_series.iloc[-1]:.4f}")
        ok(f"MACD    last value: {macd_line.iloc[-1]:.4f}  signal: {signal_line.iloc[-1]:.4f}  hist: {histogram.iloc[-1]:.4f}")

        # ── Group 2: Ross Cameron First Dip ──────────────────────────────────
        print("\n  Group 2 — Ross Cameron 'First Dip' Indicators")
        print(f"  Verify on TradingView: open {first_symbol} → 5Min chart → add VWAP\n")

        # VWAP and first_dip_signal must receive one session's bars only — VWAP
        # resets at the start of each session. Use the most recent date that has
        # data so the smoke test works on weekends and after market close too.
        idx_et = df.index.tz_convert(ET)
        last_date = idx_et.date.max()
        today_df = df[idx_et.date == last_date]

        vwap_series = vwap(today_df)
        rel_vol     = relative_volume(today_df, lookback_bars=20)
        dip_signal  = first_dip_signal(today_df, ema_period=9)

        # gap percent: most recent session's open vs the prior session's last close
        prior_df   = df[idx_et.date < last_date]
        prev_close = prior_df["close"].iloc[-1] if not prior_df.empty else today_df["close"].iloc[0]
        open_price = today_df["open"].iloc[0]
        gap_pct    = gap_percent(open_price, prev_close)

        # ── Full session table for manual verification ───────────────────────
        # Shows every bar of the most recent session with close, VWAP, EMA9,
        # volume, and signal columns so you can cross-check against TradingView.
        ema9_today     = ema(today_df["close"], period=9)
        support_series = pd.concat([vwap_series, ema9_today], axis=1).max(axis=1)

        verify_df = today_df[["open", "high", "low", "close", "volume"]].copy()
        verify_df["VWAP"]          = vwap_series.round(4)
        verify_df["EMA9"]          = ema9_today.round(4)
        verify_df["support"]       = support_series.round(4)
        verify_df["above_support"] = (verify_df["close"] > verify_df["support"])
        verify_df["dip_low"]       = (verify_df["low"] <= verify_df["support"])
        verify_df.index            = verify_df.index.tz_convert(ET)

        print(f"\n  Most recent session: {last_date}  ({len(today_df)} bars)")
        print(f"  TO VERIFY relative_volume: compare 'volume' of each bar to the")
        print(f"  average of the 20 bars before it. Current bar rel_vol = {rel_vol:.2f}x")
        print(f"\n  TO VERIFY first_dip_signal: look for the pattern below —")
        print(f"    1. above_support=True appears (the surge)")
        print(f"    2. dip_low=True on the FIRST bar that touches support")
        print(f"    3. that bar's close >= support (reclaim)")
        print(f"    4. no prior bar already completed a full dip+recovery")
        print()
        print(verify_df.to_string())
        print()

        ok(f"VWAP last value:       {vwap_series.iloc[-1]:.4f}")
        ok(f"Relative volume:       {rel_vol:.2f}x  (Ross Cameron target: >2x)")
        ok(f"Gap percent (session open vs prior close): {gap_pct * 100:.2f}%")
        ok(f"First dip signal:      {dip_signal}  (True = setup detected on last bar)")

    # ── 5. Strategies  [strategy layer] ───────────────────────────────────────
    section("5 / Strategies  [strategy.momentum + strategy.first_dip]")
    if df is None or df.empty:
        print("  (skipped — no bars available)")
    else:
        from strategy.first_dip import FirstDipStrategy
        from strategy.momentum import MomentumStrategy

        idx_et    = df.index.tz_convert(ET)
        last_date = idx_et.date.max()
        today_df  = df[idx_et.date == last_date]

        # ── MomentumStrategy ─────────────────────────────────────────────────
        print("\n  MomentumStrategy (RSI + MACD + EMA)")
        momentum  = MomentumStrategy()
        m_result  = momentum.generate_signal(first_symbol, df, today_df)
        arrow     = "→ BUY" if m_result.direction.value == "BUY" else "→ NONE"
        ok(f"{arrow}  {m_result.reason}")

        # ── FirstDipStrategy (no float filter in smoke test) ─────────────────
        print("\n  FirstDipStrategy (Ross Cameron Gap & Go)")
        print("  Note: float filter disabled in smoke test (no live API call)")
        first_dip = FirstDipStrategy(float_fetcher=None)
        fd_result = first_dip.generate_signal(first_symbol, df, today_df)
        arrow     = "→ BUY" if fd_result.direction.value == "BUY" else "→ NONE"
        ok(f"{arrow}  {fd_result.reason}")

    # ── 6. Chart ──────────────────────────────────────────────────────────────
    section("6 / Chart")
    if df is None or df.empty:
        print("  (skipped — no bars to plot)")
    else:
        # mplfinance requires capitalised column names
        plot_df = df.rename(columns=str.capitalize)
        print(plot_df.head())
        ok(f"Opening candlestick chart for {first_symbol}…")
        mpf.plot(plot_df, type="candle", volume=True, title=f"{first_symbol} — 5Min (last 30 days)", style="charles", warn_too_much_data=len(plot_df) + 1)

    # ── Done ─────────────────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print("  Done.\n")
    client.close()


if __name__ == "__main__":
    main()
