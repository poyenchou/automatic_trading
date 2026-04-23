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

import csv
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog

from broker.client import AlpacaClient
from config.settings import Settings
from market_data.models import ScreenerResult

log = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")
_LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")

_EXCHANGES = {"NASDAQ", "NYSE"}

# Symbol suffixes that indicate non-common-stock instruments:
# W = warrants, R = rights, U = units, P/PR = preferred shares,
# WS = warrants (alternate suffix), WW = warrants
_EXCLUDED_SUFFIXES = ("W", "R", "U", "WS", "WW")
_EXCLUDED_CONTAINS = (".PR", ".WS", ".U", ".RT", ".WT")


def _is_common_stock(symbol: str) -> bool:
    """Return False for warrants, rights, units, and preferred shares."""
    upper = symbol.upper()
    if any(upper.endswith(s) for s in _EXCLUDED_SUFFIXES):
        return False
    if any(s in upper for s in _EXCLUDED_CONTAINS):
        return False
    return True


class GapScreener:
    def __init__(self, client: AlpacaClient, settings: Settings) -> None:
        self._client   = client
        self._settings = settings

    def get_gappers(self) -> list[ScreenerResult]:
        """
        Fetch all NASDAQ/NYSE tradable stocks, compute gap %, and return
        those that meet the gap and price thresholds, sorted by gap % desc.

        Also writes a daily CSV to logs/screener_YYYY-MM-DD.csv with every
        evaluated symbol and the reason it passed or was filtered.

        Returns [] if no qualifying symbols are found or on empty response.
        BrokerError subclasses propagate to the caller.
        """
        # ── Step 1: Full asset universe ───────────────────────────────────
        assets = self._client.get_assets()
        symbols = [
            a["symbol"]
            for a in assets
            if a.get("exchange") in _EXCHANGES
            and a.get("tradable")
            and _is_common_stock(a["symbol"])
        ]
        log.info("screener.assets", total=len(symbols))

        # ── Step 2: Fetch snapshots in batches ────────────────────────────
        batch_size = self._settings.snapshot_batch_size
        gappers: list[ScreenerResult] = []
        csv_rows: list[dict] = []

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            snapshots = self._client.get_snapshots(batch)

            for symbol, snap in snapshots.items():
                prev_bar   = snap.get("prevDailyBar") or {}
                daily_bar  = snap.get("dailyBar") or {}
                latest     = snap.get("latestTrade") or {}

                prev_close  = prev_bar.get("c")
                daily_open  = daily_bar.get("o")
                volume      = prev_bar.get("v", 0.0)   # yesterday's full-day volume
                price       = latest.get("p") or daily_bar.get("c") or daily_open

                if not prev_close or not daily_open or prev_close <= 0:
                    continue

                gap_pct = (daily_open - prev_close) / prev_close

                # Determine filter reason for CSV
                if gap_pct < self._settings.gap_min_pct:
                    filter_reason = f"gap {gap_pct*100:.1f}% < min {self._settings.gap_min_pct*100:.0f}%"
                elif price is None or price < self._settings.min_stock_price:
                    filter_reason = f"price ${price:.2f} < min ${self._settings.min_stock_price:.2f}"
                elif volume < self._settings.min_daily_volume:
                    filter_reason = f"volume {volume:,.0f} < min {self._settings.min_daily_volume:,}"
                else:
                    filter_reason = "PASS"

                csv_rows.append({
                    "symbol":     symbol,
                    "gap_pct":    f"{gap_pct*100:.2f}",
                    "prev_close": f"{prev_close:.4f}" if prev_close else "",
                    "open":       f"{daily_open:.4f}" if daily_open else "",
                    "price":      f"{price:.4f}" if price else "",
                    "volume":     f"{volume:.0f}",
                    "result":     filter_reason,
                })

                if filter_reason != "PASS":
                    continue

                gappers.append(ScreenerResult(
                    symbol=symbol,
                    volume=float(volume),
                    gap_pct=gap_pct,
                ))

        gappers.sort(key=lambda r: r.gap_pct, reverse=True)
        log.info("screener.gappers", count=len(gappers), symbols=[r.symbol for r in gappers])

        # ── Step 3: Write daily CSV ───────────────────────────────────────
        self._write_csv(csv_rows)

        return gappers

    def _write_csv(self, rows: list[dict]) -> None:
        """Write screener results to logs/screener_YYYY-MM-DD.csv."""
        if not rows:
            return
        date_str = datetime.now(ET).strftime("%Y-%m-%d")
        os.makedirs(_LOGS_DIR, exist_ok=True)
        path = os.path.join(_LOGS_DIR, f"screener_{date_str}.csv")
        # Sort: PASS rows first (by gap desc), then filtered rows (by gap desc)
        rows_pass    = sorted([r for r in rows if r["result"] == "PASS"],
                              key=lambda r: float(r["gap_pct"]), reverse=True)
        rows_filtered = sorted([r for r in rows if r["result"] != "PASS"],
                               key=lambda r: float(r["gap_pct"]), reverse=True)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["symbol", "gap_pct", "prev_close", "open", "price", "volume", "result"]
            )
            writer.writeheader()
            writer.writerows(rows_pass + rows_filtered)
        log.info("screener.csv_written", path=path, total=len(rows), passed=len(rows_pass))
