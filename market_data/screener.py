"""
GapScreener — finds stocks gapping up pre-market using Alpaca snapshots.

Replaces the TopMoversScreener which used Alpaca's most-actives endpoint.
That endpoint returned large-caps and sub-penny stocks by share volume —
neither fits the Ross Cameron Gap & Go setup.

New approach:
  1. Fetch all NASDAQ/NYSE tradable assets (~8,000 symbols) — 1 API call
  2. Fetch snapshots in batches (prev_close + daily_open) — ~80 API calls
  3. Compute gap % = (daily_open - prev_close) / prev_close
  4. Return symbols where gap >= gap_min_pct AND price >= min_stock_price,
     sorted by gap % descending

This surfaces the correct universe: stocks with a real pre-market catalyst,
before any float or signal checks run.
"""

import structlog

from broker.client import AlpacaClient
from config.settings import Settings
from market_data.models import ScreenerResult

log = structlog.get_logger(__name__)

_EXCHANGES = {"NASDAQ", "NYSE"}


class GapScreener:
    def __init__(self, client: AlpacaClient, settings: Settings) -> None:
        self._client   = client
        self._settings = settings

    def get_gappers(self) -> list[ScreenerResult]:
        """
        Fetch all NASDAQ/NYSE tradable stocks, compute gap %, and return
        those that meet the gap and price thresholds, sorted by gap % desc.

        Returns [] if no qualifying symbols are found or on empty response.
        BrokerError subclasses propagate to the caller.
        """
        # ── Step 1: Full asset universe ───────────────────────────────────
        assets = self._client.get_assets()
        symbols = [
            a["symbol"]
            for a in assets
            if a.get("exchange") in _EXCHANGES and a.get("tradable")
        ]
        log.info("screener.assets", total=len(symbols))

        # ── Step 2: Fetch snapshots in batches ────────────────────────────
        batch_size = self._settings.snapshot_batch_size
        gappers: list[ScreenerResult] = []

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            snapshots = self._client.get_snapshots(batch)

            for symbol, snap in snapshots.items():
                prev_bar   = snap.get("prevDailyBar") or {}
                daily_bar  = snap.get("dailyBar") or {}
                latest     = snap.get("latestTrade") or {}

                prev_close  = prev_bar.get("c")
                daily_open  = daily_bar.get("o")
                volume      = daily_bar.get("v", 0.0)
                price       = latest.get("p") or daily_bar.get("c") or daily_open

                if not prev_close or not daily_open or prev_close <= 0:
                    continue

                gap_pct = (daily_open - prev_close) / prev_close

                if gap_pct < self._settings.gap_min_pct:
                    continue

                if price is None or price < self._settings.min_stock_price:
                    continue

                gappers.append(ScreenerResult(
                    symbol=symbol,
                    volume=float(volume),
                    gap_pct=gap_pct,
                ))

        gappers.sort(key=lambda r: r.gap_pct, reverse=True)
        log.info("screener.gappers", count=len(gappers), symbols=[r.symbol for r in gappers])
        return gappers
