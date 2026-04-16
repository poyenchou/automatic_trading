"""
MorningWorkflow — wires all layers together for a single morning trading session.

Run order:
  1. Screener      — fetch gappers once at startup
  2. Float filter  — skip high-float stocks once per symbol
  3. Scan loop     — repeat every scan_interval_seconds until 10:30 AM ET:
       a. Fetch fresh 5-min bars per candidate
       b. Evaluate strategies — place order immediately on first BUY
       c. Stop early once max_concurrent_positions are filled
  4. Monitor       — watch all open positions concurrently until closed

Screener and float filter run once because gap % and float don't change
during the session. Bars and signals are re-fetched each scan so the
strategy sees the latest price action as the first-dip pattern develops.
"""

import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from zoneinfo import ZoneInfo

def _parse_time(s: str) -> dt_time:
    """Parse 'HH:MM' into a datetime.time."""
    h, m = s.split(":")
    return dt_time(int(h), int(m))

import structlog

from broker.client import AlpacaClient
from config.settings import Settings
from execution.models import PositionState
from execution.order_manager import OrderManager
from execution.position_monitor import PositionMonitor
from market_data.float_filter import DEFAULT_MAX_FLOAT, FloatFetcher
from market_data.history import HistoricalDataFetcher
from market_data.screener import GapScreener
from strategy.base import Strategy
from strategy.models import Direction, SignalResult

log = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")

# FirstDipStrategy prime window closes at 10:30 AM ET
_PRIME_WINDOW_CLOSE = dt_time(10, 30)


@dataclass
class TradeResult:
    symbol: str
    signal: SignalResult
    outcome: str          # "tp", "sl", "manual", or "skipped"
    reason: str = ""


class MorningWorkflow:
    def __init__(
        self,
        client: AlpacaClient,
        settings: Settings,
        strategies: list[Strategy],
        float_fetcher: FloatFetcher | None = None,
    ) -> None:
        self._client        = client
        self._settings      = settings
        self._strategies    = strategies
        self._float_fetcher = float_fetcher
        self._screener      = GapScreener(client=client, settings=settings)
        self._fetcher       = HistoricalDataFetcher(client=client)
        self._order_manager = OrderManager(client=client, settings=settings)
        self._monitor       = PositionMonitor(
            client=client,
            poll_interval_seconds=settings.poll_interval_seconds,
            exit_time_et=_parse_time(settings.monitor_exit_time),
        )

    def run(self) -> list[TradeResult]:
        """
        Execute the full morning workflow.

        Returns a list of TradeResult — one per candidate symbol (skipped or
        traded), plus monitoring outcomes for any positions that were opened.
        """
        results: list[TradeResult] = []

        # ── Step 1: Screener (once) ───────────────────────────────────────
        log.info("workflow.screener.start")
        movers = self._screener.get_gappers()
        log.info("workflow.screener.done", count=len(movers), symbols=[m.symbol for m in movers])

        # ── Step 2: Float filter (once per symbol) ────────────────────────
        candidates: list[str] = []
        for mover in movers:
            symbol = mover.symbol
            if self._float_fetcher is not None:
                float_shares = self._float_fetcher.get_float_shares(symbol)
                actual_str = f"{float_shares / 1_000_000:.1f}M" if float_shares is not None else "unknown"
                if float_shares is None or float_shares > DEFAULT_MAX_FLOAT:
                    log.info(
                        "workflow.float_filter.skip",
                        symbol=symbol,
                        actual_float=actual_str,
                        max_float=f"{DEFAULT_MAX_FLOAT / 1_000_000:.0f}M",
                    )
                    results.append(TradeResult(
                        symbol=symbol, signal=_none_signal(symbol),
                        outcome="skipped",
                        reason=f"float {actual_str} above maximum {DEFAULT_MAX_FLOAT / 1_000_000:.0f}M shares",
                    ))
                    continue
            candidates.append(symbol)

        if not candidates:
            log.info("workflow.no_candidates")
            return results

        # ── Step 3: Scan loop ─────────────────────────────────────────────
        # Rescan candidates every scan_interval_seconds until the prime
        # window closes (10:30 AM ET) or max_concurrent_positions are filled.
        positions: list[tuple[str, SignalResult, PositionState]] = []
        traded: set[str] = set()        # symbols already ordered or permanently skipped
        last_signal: dict[str, SignalResult] = {}

        while self._prime_window_open():
            if len(positions) >= self._settings.max_concurrent_positions:
                log.info("workflow.max_positions_reached", max=self._settings.max_concurrent_positions)
                break

            log.info("workflow.scan", time_et=datetime.now(ET).strftime("%H:%M ET"),
                     candidates=len(candidates) - len(traded))

            for symbol in candidates:
                if symbol in traded:
                    continue
                if len(positions) >= self._settings.max_concurrent_positions:
                    break

                df, today_df = self._fetch_bars(symbol)
                if df is None:
                    continue

                signal = self._evaluate_strategies(symbol, df, today_df)
                last_signal[symbol] = signal
                log.info("workflow.signal", symbol=symbol,
                         direction=signal.direction.value, reason=signal.reason)

                if signal.direction != Direction.BUY:
                    continue

                # BUY — place order immediately
                try:
                    current_price = float(df["close"].iloc[-1])
                    request = self._order_manager.build_order_request(
                        symbol, current_price, stop_price=signal.stop_price
                    )
                    state   = self._order_manager.execute(request)
                    positions.append((symbol, signal, state))
                    traded.add(symbol)
                    log.info("workflow.order_placed", symbol=symbol)
                except Exception as exc:
                    log.error("workflow.order_failed", symbol=symbol, error=str(exc))
                    results.append(TradeResult(
                        symbol=symbol, signal=signal,
                        outcome="skipped", reason=f"order failed: {exc}",
                    ))
                    traded.add(symbol)

            if len(positions) >= self._settings.max_concurrent_positions:
                break

            # Sleep until next scan, waking up no later than window close
            sleep_secs = max(0.0, self._sleep_until_next_scan())
            log.info("workflow.scan.sleep", seconds=int(sleep_secs))
            time_module.sleep(sleep_secs)

        # Symbols that never produced a BUY get a final skipped entry
        for symbol in candidates:
            if symbol not in traded:
                sig = last_signal.get(symbol, _none_signal(symbol))
                results.append(TradeResult(
                    symbol=symbol, signal=sig,
                    outcome="skipped",
                    reason=sig.reason or "no signal during prime window",
                ))

        if not positions:
            log.info("workflow.no_buy_signals")
            return results

        # ── Step 4: Monitor positions concurrently ────────────────────────
        trade_results: list[TradeResult] = []
        lock = threading.Lock()

        def _monitor_one(symbol: str, signal: SignalResult, state: PositionState) -> None:
            try:
                outcome = self._monitor.monitor(state)
            except Exception as exc:
                log.error("workflow.monitor_crashed", symbol=symbol, error=str(exc))
                outcome = "error"
            with lock:
                trade_results.append(TradeResult(symbol=symbol, signal=signal, outcome=outcome))

        threads = [
            threading.Thread(target=_monitor_one, args=(sym, sig, st), daemon=True)
            for sym, sig, st in positions
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        results.extend(trade_results)
        log.info("workflow.done", trades=len(trade_results))
        return results

    # ------------------------------------------------------------------
    # Helpers (extracted for testability)
    # ------------------------------------------------------------------

    def _prime_window_open(self) -> bool:
        """Return True if we are still within the 10:30 AM ET prime window."""
        return datetime.now(ET).time() < _PRIME_WINDOW_CLOSE

    def _sleep_until_next_scan(self) -> float:
        """Seconds to sleep before next scan, capped at window close."""
        now_et = datetime.now(ET)
        window_close_dt = datetime.combine(now_et.date(), _PRIME_WINDOW_CLOSE, tzinfo=ET)
        remaining = (window_close_dt - now_et).total_seconds()
        return min(self._settings.scan_interval_seconds, remaining)

    def _fetch_bars(self, symbol: str):
        """Fetch 14 days of 5-min bars filtered to regular market hours.

        Returns (df, today_df) or (None, None) on failure / empty data.
        """
        try:
            start = (datetime.now(timezone.utc) - timedelta(days=14)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            df = self._fetcher.fetch_bars(symbol, timeframe="5Min", start=start, limit=2000)
        except Exception as exc:
            log.warning("workflow.history.fetch_failed", symbol=symbol, error=str(exc))
            return None, None

        if df.empty:
            log.warning("workflow.history.empty", symbol=symbol)
            return None, None

        df.index = df.index.tz_convert(ET)
        df = df.between_time("09:30", "15:55")
        df.index = df.index.tz_convert("UTC")

        idx_et    = df.index.tz_convert(ET)
        last_date = idx_et.date.max()
        today_df  = df[idx_et.date == last_date]

        if today_df.empty:
            log.warning("workflow.history.no_today_bars", symbol=symbol)
            return None, None

        return df, today_df

    def _evaluate_strategies(self, symbol: str, df, today_df) -> SignalResult:
        """Run all strategies, return first BUY found or last NONE."""
        last_result = _none_signal(symbol)
        for strategy in self._strategies:
            result = strategy.generate_signal(symbol, df, today_df)
            last_result = result
            if result.direction == Direction.BUY:
                return result
        return last_result


def _none_signal(symbol: str) -> SignalResult:
    from strategy.models import SignalResult
    return SignalResult(symbol=symbol, direction=Direction.NONE, reason="")
