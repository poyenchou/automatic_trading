"""
First Dip Strategy — Ross Cameron Gap & Go / First Pullback setup.

Entry logic (all conditions must be true):
  Pre-market gate (checked once per symbol):
    1. Low float (< 20M shares) — small float amplifies price moves
    2. Relative volume > 2x — confirms a real catalyst, not a thin gap

  Note: gap up % is pre-filtered by GapScreener before this strategy runs.

  Intraday entry (checked on each new bar):
    3. Within prime window: 9:30–10:30 AM ET
    4. First dip signal OR opening range breakout

If the float filter is not provided (None), condition 1 is skipped.
No broker imports allowed in this module.
"""

from zoneinfo import ZoneInfo

import pandas as pd

from strategy.base import Strategy
from strategy.models import Direction, SignalResult
from strategy.signals import (
    first_dip_signal,
    gap_percent,
    in_prime_window,
    opening_range_breakout,
    relative_volume,
)


ET = ZoneInfo("America/New_York")

# Default thresholds
MIN_GAP_PCT      = 0.10   # 10% gap up minimum
MIN_REL_VOL      = 2.0    # 2x relative volume minimum
MIN_BARS_TODAY   = 3      # need at least 3 bars for first_dip_signal


class FirstDipStrategy(Strategy):
    """
    Ross Cameron Gap & Go / First Pullback strategy.

    Uses Group 2 signals exclusively. Requires both a full history DataFrame
    (for relative_volume lookback) and a session DataFrame (for VWAP/first_dip).

    Args:
        float_fetcher: Optional FloatFetcher instance. If None, the low-float
                       filter is skipped (useful for testing or when float data
                       is unavailable).
        min_gap_pct:   Minimum gap up as a fraction (default 0.10 = 10%).
        min_rel_vol:   Minimum relative volume multiplier (default 2.0).
        max_float:     Maximum float shares for low-float filter (default 20M).
        ema_period:    EMA period used as support line in first_dip_signal (default 9).
        range_bars:    Opening range bar count for opening_range_breakout (default 1).
    """

    def __init__(
        self,
        float_fetcher=None,
        min_gap_pct: float = MIN_GAP_PCT,
        min_rel_vol: float = MIN_REL_VOL,
        max_float: int = 20_000_000,
        ema_period: int = 9,
        range_bars: int = 1,
    ) -> None:
        self._float_fetcher = float_fetcher
        self._min_gap_pct   = min_gap_pct
        self._min_rel_vol   = min_rel_vol
        self._max_float     = max_float
        self._ema_period    = ema_period
        self._range_bars    = range_bars

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        today_df: pd.DataFrame,
    ) -> SignalResult:
        """
        Compute a first-dip signal for the given symbol.

        Args:
            symbol:   Stock ticker.
            df:       Full history DataFrame (30 days, UTC index).
                      Used for relative_volume lookback.
            today_df: Current session bars only (UTC index).
                      Used for gap_percent, vwap, first_dip_signal.

        Returns:
            SignalResult(BUY) if all conditions are met, else NONE.
        """
        def none(reason: str) -> SignalResult:
            return SignalResult(symbol=symbol, direction=Direction.NONE, reason=reason)

        if today_df.empty:
            return none("no session bars available")

        # ── Gap % (informational only — pre-filtered by GapScreener) ────────
        today_dates = today_df.index.tz_convert(ET).date
        prior_df    = df[df.index.tz_convert(ET).date < today_dates.min()]
        if not prior_df.empty:
            open_price = today_df["open"].iloc[0]
            prev_close = prior_df["close"].iloc[-1]
            gap_pct    = gap_percent(open_price, prev_close)
        else:
            gap_pct = 0.0

        # ── Gate 1: Low float ────────────────────────────────────────────────
        if self._float_fetcher is not None:
            if not self._float_fetcher.is_low_float(symbol, max_float=self._max_float):
                return none(
                    f"float above {self._max_float:,} shares threshold"
                )

        # ── Gate 2: Relative volume ──────────────────────────────────────────
        rel_vol = relative_volume(today_df, lookback_bars=20)
        if rel_vol < self._min_rel_vol:
            return none(
                f"relative volume {rel_vol:.2f}x below minimum {self._min_rel_vol:.1f}x"
            )

        # ── Gate 3: Prime window ─────────────────────────────────────────────
        last_ts = today_df.index[-1]
        if not in_prime_window(last_ts, ET):
            return none(
                f"outside prime window 9:30–10:30 AM ET "
                f"(current bar: {last_ts.tz_convert(ET).strftime('%H:%M')} ET)"
            )

        # ── Entry: First dip OR opening range breakout ───────────────────────
        if len(today_df) < MIN_BARS_TODAY:
            return none(f"insufficient session bars: {len(today_df)} < {MIN_BARS_TODAY}")

        dip    = first_dip_signal(today_df, ema_period=self._ema_period)
        breakout = opening_range_breakout(today_df, range_bars=self._range_bars)

        if dip:
            return SignalResult(
                symbol=symbol,
                direction=Direction.BUY,
                reason=(
                    f"first dip setup: gap={gap_pct * 100:.1f}%, "
                    f"rel_vol={rel_vol:.2f}x, "
                    f"price reclaimed VWAP/EMA({self._ema_period})"
                ),
            )

        if breakout:
            return SignalResult(
                symbol=symbol,
                direction=Direction.BUY,
                reason=(
                    f"opening range breakout: gap={gap_pct * 100:.1f}%, "
                    f"rel_vol={rel_vol:.2f}x, "
                    f"price broke above {self._range_bars}-bar opening range"
                ),
            )

        return none(
            f"gap={gap_pct * 100:.1f}%, rel_vol={rel_vol:.2f}x — "
            f"waiting for first dip or breakout"
        )
