"""
Momentum Strategy — Group 1 indicators only (RSI + MACD + EMA trend filter).

Entry logic:
  - RSI(14) is oversold (< 40) indicating a potential bounce
  - MACD histogram is positive and increasing (momentum turning up)
  - Price is above EMA(20) (confirming the broader uptrend)

All three conditions must be true for a BUY signal.
No broker imports allowed in this module.
"""

import pandas as pd

from strategy.base import Strategy
from strategy.models import Direction, SignalResult
from strategy.signals import ema, macd, rsi

# Thresholds — adjust these to tune the strategy
RSI_OVERSOLD     = 40   # buy when RSI is below this (momentum dip in an uptrend)
MIN_BARS         = 50   # minimum bars needed for indicators to be meaningful


class MomentumStrategy(Strategy):
    """
    RSI + MACD + EMA trend filter strategy.

    Uses Group 1 general momentum indicators. Does not use VWAP, float,
    or any session-specific signals — those belong to FirstDipStrategy.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        ema_fast: int = 9,
        ema_slow: int = 20,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_oversold: float = RSI_OVERSOLD,
    ) -> None:
        self._rsi_period   = rsi_period
        self._ema_fast     = ema_fast
        self._ema_slow     = ema_slow
        self._macd_fast    = macd_fast
        self._macd_slow    = macd_slow
        self._macd_signal  = macd_signal
        self._rsi_oversold = rsi_oversold

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        today_df: pd.DataFrame,
    ) -> SignalResult:
        """
        Compute a momentum signal for the given symbol.

        Args:
            symbol:   Stock ticker.
            df:       Full history DataFrame (30 days recommended for RSI warm-up).
            today_df: Not used by this strategy (session-agnostic).

        Returns:
            SignalResult(BUY) if all three conditions are met, else NONE.
        """
        def none(reason: str) -> SignalResult:
            return SignalResult(symbol=symbol, direction=Direction.NONE, reason=reason)

        if len(df) < MIN_BARS:
            return none(f"insufficient bars: {len(df)} < {MIN_BARS}")

        close = df["close"]

        # ── Indicator 1: RSI ─────────────────────────────────────────────────
        rsi_series = rsi(close, period=self._rsi_period)
        rsi_val = rsi_series.dropna().iloc[-1] if not rsi_series.dropna().empty else float("nan")

        if rsi_val != rsi_val:  # NaN check
            return none("RSI could not be computed")

        if rsi_val >= self._rsi_oversold:
            return none(f"RSI {rsi_val:.1f} not oversold (threshold < {self._rsi_oversold})")

        # ── Indicator 2: MACD histogram positive and increasing ──────────────
        _, _, histogram = macd(
            close,
            fast=self._macd_fast,
            slow=self._macd_slow,
            signal=self._macd_signal,
        )
        hist_clean = histogram.dropna()
        if len(hist_clean) < 2:
            return none("insufficient data for MACD")

        hist_now  = hist_clean.iloc[-1]
        hist_prev = hist_clean.iloc[-2]

        if hist_now <= 0:
            return none(f"MACD histogram not positive ({hist_now:.4f})")
        if hist_now <= hist_prev:
            return none(f"MACD histogram not increasing ({hist_prev:.4f} → {hist_now:.4f})")

        # ── Indicator 3: price above slow EMA (trend filter) ─────────────────
        ema_slow_series = ema(close, period=self._ema_slow)
        ema_slow_val    = ema_slow_series.iloc[-1]
        price_now       = close.iloc[-1]

        if price_now <= ema_slow_val:
            return none(
                f"price {price_now:.4f} below EMA({self._ema_slow}) {ema_slow_val:.4f}"
            )

        return SignalResult(
            symbol=symbol,
            direction=Direction.BUY,
            reason=(
                f"RSI({self._rsi_period})={rsi_val:.1f} oversold, "
                f"MACD histogram positive and increasing ({hist_prev:.4f}→{hist_now:.4f}), "
                f"price {price_now:.4f} above EMA({self._ema_slow}) {ema_slow_val:.4f}"
            ),
        )
