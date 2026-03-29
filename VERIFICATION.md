# Manual Verification Checklist

Items that need real-market data to verify and cannot be confirmed by unit tests alone.

---

## Already Verified ✅
- RSI(14) — matches Yahoo Finance
- EMA(9), EMA(20) — matches Yahoo Finance
- MACD(12,26,9) — matches Yahoo Finance
- VWAP (intraday, session-anchored) — matches Yahoo Finance

---

## Needs Verification

### `relative_volume` — medium confidence
**What to check:**
Run the smoke test on a trading day. In the session table printed under Group 2,
pick any bar and manually verify:

1. Take the `volume` of that bar
2. Find the 20 bars immediately before it in the table
3. Compute their average volume
4. Divide: `bar_volume / avg_volume` — should match the printed `rel_vol` value

**Known edge case to watch:**
If the session has fewer than 20 bars (e.g. early in the day), the function
returns `0.0`. Confirm this is acceptable behaviour or decide on a fallback.

---

### `first_dip_signal` — low confidence
**What to check:**
Run the smoke test on a day where a top mover had a clear first-dip setup.
In the session table, manually trace the four conditions:

1. Find the first bar where `above_support=True` → this is the surge
2. Find the first bar after that where `dip_low=True` → this is the dip
3. Confirm that bar's `close >= support` → this is the reclaim
4. Confirm no earlier bar already completed a full dip+recovery cycle
5. Confirm `first_dip_signal = True` is printed for that session

**Best way to visually confirm:**
Open the symbol on Yahoo Finance → select 1D chart → set interval to 5Min → add VWAP and EMA(9).
Find the bar where price tagged VWAP/EMA and closed back above it.
That bar's timestamp should match where `dip_low=True` AND `above_support=True`
appear together in the smoke test table.

**Known limitation:**
The signal only returns True/False for the LAST bar of the DataFrame.
During live trading this is fine (you check on each new bar). In the smoke test,
if the session ended without a dip setup, the signal will always be False —
you may need to run on multiple symbols to catch a True case.

---

### `opening_range_breakout` — medium confidence
**What to check:**
1. Confirm the first bar in `today_df` is the 9:30 AM ET candle (check the
   timestamp in the session table — should be `09:30:00-04:00`)
2. Pick a session where the stock broke out. Find the bar where
   `close > high of the 9:30 candle`. Confirm the signal would fire there.

---

### `gap_percent` — high confidence (arithmetic only)
**What to check:**
From the smoke test output:
- Note the printed `prev_close` (last close of the prior session)
- Note `open_price` (first open of the current session)
- Manually compute: `(open - prev_close) / prev_close * 100`
- Should match the printed gap percent

---

## Future Concern
`FloatFetcher` uses yfinance which is unofficial. Before going live, switch to
a paid reliable endpoint. See `market_data/float_filter.py` for alternatives.
