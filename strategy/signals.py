"""
Indicator functions for strategy computation.

Two groups:
  Group 1 — General Momentum Indicators (rsi, ema, macd)
  Group 2 — Ross Cameron "First Dip" Indicators (vwap, gap_percent,
             relative_volume, first_dip_signal, in_prime_window,
             opening_range_breakout)

All functions are pure: they accept pandas Series/DataFrames and return
computed values with no side effects. No broker imports.
"""

import datetime
import pandas as pd


# ---------------------------------------------------------------------------
# Group 1 — General Momentum Indicators
# ---------------------------------------------------------------------------

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing (matches Yahoo Finance / TradingView).

    The first average is seeded with a simple mean of the first `period` gains/losses,
    then Wilder's exponential smoothing (alpha = 1/period) is applied for all
    subsequent bars. Values before the seed bar are NaN.

    Args:
        series: Closing prices (or any price series).
        period: Look-back window (default 14).
    """
    if period < 1:
        raise ValueError("period must be >= 1")

    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.copy().astype(float) * float("nan")
    avg_loss = loss.copy().astype(float) * float("nan")

    if len(series) <= period:
        return avg_gain  # not enough data — return all NaN

    # Seed: simple mean of the first `period` changes (bars 1..period)
    avg_gain.iloc[period] = gain.iloc[1 : period + 1].mean()
    avg_loss.iloc[period] = loss.iloc[1 : period + 1].mean()

    # Wilder's smoothing: avg = prev_avg * (period-1)/period + current * 1/period
    alpha = 1.0 / period
    for i in range(period + 1, len(series)):
        avg_gain.iloc[i] = avg_gain.iloc[i - 1] * (1 - alpha) + gain.iloc[i] * alpha
        avg_loss.iloc[i] = avg_loss.iloc[i - 1] * (1 - alpha) + loss.iloc[i] * alpha

    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    all_gain_mask = (avg_loss == 0) & avg_gain.notna()
    rsi_series[all_gain_mask] = 100.0
    return rsi_series


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.

    Args:
        series: Input price series.
        period: Span (number of bars) for the EMA.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.ewm(span=period, adjust=False).mean()


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD — Moving Average Convergence/Divergence.

    Returns:
        macd_line:   Fast EMA minus slow EMA.
        signal_line: EMA of the MACD line.
        histogram:   macd_line minus signal_line.

    Args:
        series: Closing prices.
        fast:   Fast EMA period (default 12).
        slow:   Slow EMA period (default 26).
        signal: Signal EMA period (default 9).
    """
    if fast >= slow:
        raise ValueError("fast period must be less than slow period")

    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Group 2 — Ross Cameron "First Dip" Indicators
#
# Strategy: Gap & Go / First Pullback
#   1. Stock gaps up significantly vs prior close (catalyst + high rel. volume)
#   2. Price surges above VWAP on open
#   3. First pullback to VWAP or 9 EMA — do NOT chase second/third dips
#   4. Price reclaims the level → BUY signal
#   Reference: https://www.youtube.com/watch?v=oxob0x0Xz7s
# ---------------------------------------------------------------------------

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Anchored VWAP from the first bar of the session.

    Computed as the cumulative (price * volume) / cumulative volume, where
    price = (high + low + close) / 3 (typical price).

    Args:
        df: Intraday OHLCV DataFrame with columns open/high/low/close/volume.
            Must be sorted in ascending time order.

    Returns:
        Series of VWAP values aligned to df's index.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (typical * df["volume"]).cumsum()
    return cum_tp_vol / cum_vol


def gap_percent(open_price: float, prev_close: float) -> float:
    """
    Pre-market gap as a fraction of prior close.

    Positive value = gap up, negative = gap down.
    E.g. 0.05 means the stock opened 5% above yesterday's close.

    Args:
        open_price: Today's opening price.
        prev_close: Yesterday's closing price.
    """
    if prev_close <= 0:
        raise ValueError("prev_close must be positive")
    return (open_price - prev_close) / prev_close


def relative_volume(
    df: pd.DataFrame,
    today_df: pd.DataFrame,
    lookback_days: int = 10,
) -> float:
    """
    Relative volume of the most recent bar vs the same time-of-day average
    across prior sessions.

    Ross Cameron's definition: "Is today's 9:35 AM bar busier than a typical
    9:35 AM bar?" This requires cross-day history — comparing within today's
    session only is meaningless during the first ~100 minutes of trading.

    Algorithm:
      1. Find the time-of-day of the last bar in today_df (e.g. 09:35 ET).
      2. Find all bars at that same time across the prior `lookback_days`
         sessions in df.
      3. Return today's volume / mean of those historical bars.

    Args:
        df:            Full history DataFrame (UTC index, regular hours only).
                       Used for cross-day lookback.
        today_df:      Current session bars only (subset of df).
                       The last bar is the one being evaluated.
        lookback_days: Number of prior trading days to average (default 10).

    Returns:
        Ratio of the current bar's volume to the historical same-time average.
        Returns 0.0 if fewer than 2 prior days have data for that time slot.
    """
    if today_df.empty:
        return 0.0

    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    # Time-of-day of the bar we're evaluating
    last_bar = today_df.index[-1]
    last_bar_time = last_bar.tz_convert(ET).time()
    current_volume = float(today_df["volume"].iloc[-1])

    # Today's date to exclude from the lookback
    today_date = last_bar.tz_convert(ET).date()

    # Find same-time bars from prior sessions in df
    idx_et = df.index.tz_convert(ET)
    same_time_mask = (idx_et.time == last_bar_time) & (idx_et.date < today_date)
    prior_bars = df[same_time_mask]["volume"]

    # Limit to the most recent lookback_days sessions
    prior_bars = prior_bars.iloc[-lookback_days:]

    if len(prior_bars) < 2:
        return 0.0

    avg = prior_bars.mean()
    if avg == 0:
        return 0.0

    return float(current_volume / avg)


def first_dip_signal(df: pd.DataFrame, ema_period: int = 9) -> tuple[bool, float | None]:
    """
    Detect the Ross Cameron "first dip" buy setup.

    Conditions (all must be true on the most recent bar):
      1. Price previously surged above VWAP (at least one prior bar closed
         above VWAP after the open).
      2. Price pulled back to or below VWAP or the 9 EMA (the dip).
      3. This is the FIRST such pullback — no prior bar already dipped to
         VWAP/EMA and recovered (i.e. we are not chasing a second dip).
      4. The current close is back at or above VWAP or the 9 EMA (reclaim),
         signalling the dip has been bought.

    Args:
        df:         Intraday OHLCV DataFrame, sorted ascending, >= 3 bars.
        ema_period: Period for the fast EMA used as the lower support line
                    (Ross Cameron uses 9 EMA on 1-min/5-min charts).

    Returns:
        Tuple of (signal_fired, dip_candle_low):
          - signal_fired:    True if the first-dip buy setup is active on the latest bar.
          - dip_candle_low:  The low of the dip candle (current bar low) when signal
                             fires; None otherwise. Used to set chart-based stop-loss.
    """
    if len(df) < 3:
        return False, None

    vwap_series = vwap(df)
    ema_series = ema(df["close"], ema_period)
    support = pd.concat([vwap_series, ema_series], axis=1).max(axis=1)

    close = df["close"]

    # Bars 1..N-2 (exclude the very first bar and the current bar)
    prior = close.iloc[1:-1]
    prior_support = support.iloc[1:-1]

    # Condition 1: at least one prior bar closed above support (the surge)
    surged = (prior > prior_support).any()
    if not surged:
        return False, None

    # Condition 3: no prior bar already dipped to/below support AND recovered
    # A completed dip = a bar that was <= support followed by a bar > support
    below = prior <= prior_support
    above = prior > prior_support
    # Shift above by 1 to check if the bar AFTER a dip recovered
    recovered_after_dip = (below & above.shift(-1, fill_value=False))
    if recovered_after_dip.any():
        return False, None

    # Condition 2 + 4: current bar dipped to/below support but closes at/above
    current_close = close.iloc[-1]
    current_low = float(df["low"].iloc[-1])
    current_support = support.iloc[-1]

    dipped = current_low <= current_support
    reclaimed = current_close >= current_support

    if dipped and reclaimed:
        return True, current_low
    return False, None


def in_prime_window(ts: pd.Timestamp, tz: datetime.tzinfo) -> bool:
    """
    True if the bar falls within Ross Cameron's prime trading window.

    Ross Cameron focuses almost exclusively on 9:30–10:30 AM ET. After 10:30,
    volatility drops, spreads widen, and the first-dip setup loses reliability.

    Args:
        ts: Timestamp of the bar (must be timezone-aware).
        tz: Target timezone to evaluate the time in (pass ET = ZoneInfo("America/New_York")).
    """
    local_time = ts.tz_convert(tz).time()
    return datetime.time(9, 30) <= local_time < datetime.time(10, 30)


def opening_range_breakout(df: pd.DataFrame, range_bars: int = 1) -> bool:
    """
    True if the current bar closes above the opening range high.

    The opening range is the high of the first `range_bars` bars of the session.
    With 5-min bars and range_bars=1, the opening range is just the first candle
    (9:30–9:35 AM). A close above that high signals continuation momentum.

    Ross Cameron uses this as an alternative entry to the first dip: if price
    never pulls back but keeps breaking to new highs, buy the breakout instead.

    Args:
        df:         Intraday OHLCV DataFrame for the session, sorted ascending.
        range_bars: Number of opening bars that define the range (default 1).
    """
    if len(df) < range_bars + 1:
        return False
    opening_high = df["high"].iloc[:range_bars].max()
    return bool(df["close"].iloc[-1] > opening_high)
