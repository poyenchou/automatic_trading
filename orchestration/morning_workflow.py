"""
MorningWorkflow — wires all layers together for a single morning trading session.

Run order:
  1. Screener      — fetch top N movers by volume
  2. Float filter  — skip high-float stocks
  3. History       — fetch 30 days of 5-min bars per symbol
  4. Signals       — run MomentumStrategy and FirstDipStrategy
  5. Orders        — place entries for up to max_concurrent_positions BUY signals
  6. Monitor       — watch all open positions concurrently until closed

Concurrency: positions are monitored in parallel threads so one slow exit
does not block the others.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog

from broker.client import AlpacaClient
from config.settings import Settings
from execution.models import PositionState
from execution.order_manager import OrderManager
from execution.position_monitor import PositionMonitor
from market_data.float_filter import FloatFetcher
from market_data.history import HistoricalDataFetcher
from market_data.screener import TopMoversScreener
from strategy.base import Strategy
from strategy.models import Direction, SignalResult

log = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


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
        self._screener      = TopMoversScreener(client=client, settings=settings)
        self._fetcher       = HistoricalDataFetcher(client=client)
        self._order_manager = OrderManager(client=client, settings=settings)
        self._monitor       = PositionMonitor(
            client=client,
            poll_interval_seconds=settings.poll_interval_seconds,
        )

    def run(self) -> list[TradeResult]:
        """
        Execute the full morning workflow.

        Returns a list of TradeResult — one per symbol that received a BUY
        signal (up to max_concurrent_positions), plus skipped symbols with
        their skip reason.
        """
        results: list[TradeResult] = []

        # ── Step 1: Screener ──────────────────────────────────────────────
        log.info("workflow.screener.start")
        movers = self._screener.get_top_movers()
        log.info("workflow.screener.done", count=len(movers))

        buy_signals: list[tuple[str, SignalResult]] = []

        for mover in movers:
            symbol = mover.symbol

            # ── Step 2: Float filter ──────────────────────────────────────
            if self._float_fetcher is not None:
                if not self._float_fetcher.is_low_float(symbol):
                    log.info("workflow.float_filter.skip", symbol=symbol)
                    results.append(TradeResult(
                        symbol=symbol, signal=_none_signal(symbol),
                        outcome="skipped", reason="high float",
                    ))
                    continue

            # ── Step 3: Fetch history ─────────────────────────────────────
            log.info("workflow.history.fetch", symbol=symbol)
            start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            import pandas as pd
            df = self._fetcher.fetch_bars(symbol, timeframe="5Min", start=start, limit=2000)
            if df.empty:
                log.warning("workflow.history.empty", symbol=symbol)
                results.append(TradeResult(
                    symbol=symbol, signal=_none_signal(symbol),
                    outcome="skipped", reason="no historical data",
                ))
                continue

            # Filter to regular market hours
            df.index = df.index.tz_convert(ET)
            df = df.between_time("09:30", "15:55")
            df.index = df.index.tz_convert("UTC")

            # Split into full history and today's session
            idx_et     = df.index.tz_convert(ET)
            last_date  = idx_et.date.max()
            today_df   = df[idx_et.date == last_date]

            if today_df.empty:
                log.warning("workflow.history.no_today_bars", symbol=symbol)
                results.append(TradeResult(
                    symbol=symbol, signal=_none_signal(symbol),
                    outcome="skipped", reason="no bars for current session",
                ))
                continue

            # ── Step 4: Price filter — skip penny stocks ──────────────────
            last_price = float(df["close"].iloc[-1])
            if last_price < self._settings.min_stock_price:
                log.info("workflow.price_filter.skip", symbol=symbol, price=last_price)
                results.append(TradeResult(
                    symbol=symbol, signal=_none_signal(symbol),
                    outcome="skipped",
                    reason=f"price ${last_price:.2f} below minimum ${self._settings.min_stock_price:.2f}",
                ))
                continue

            # ── Step 5: Signals ───────────────────────────────────────────
            signal = self._evaluate_strategies(symbol, df, today_df)
            log.info(
                "workflow.signal",
                symbol=symbol,
                direction=signal.direction.value,
                reason=signal.reason,
            )

            if signal.direction == Direction.BUY:
                buy_signals.append((symbol, signal))

            if signal.direction != Direction.BUY:
                results.append(TradeResult(
                    symbol=symbol, signal=signal,
                    outcome="skipped", reason=signal.reason,
                ))

            # Stop scanning once we have enough BUY signals
            if len(buy_signals) >= self._settings.max_concurrent_positions:
                log.info(
                    "workflow.max_positions_reached",
                    max=self._settings.max_concurrent_positions,
                )
                break

        if not buy_signals:
            log.info("workflow.no_buy_signals")
            return results

        # ── Step 6: Place orders ──────────────────────────────────────────
        positions: list[tuple[str, SignalResult, PositionState]] = []
        for symbol, signal in buy_signals:
            try:
                current_price = self._get_current_price(symbol, df)
                request = self._order_manager.build_order_request(symbol, current_price)
                state   = self._order_manager.execute(request)
                positions.append((symbol, signal, state))
                log.info("workflow.order_placed", symbol=symbol)
            except Exception as exc:
                log.error("workflow.order_failed", symbol=symbol, error=str(exc))
                results.append(TradeResult(
                    symbol=symbol, signal=signal,
                    outcome="skipped", reason=f"order failed: {exc}",
                ))

        # ── Step 7: Monitor positions concurrently ────────────────────────
        trade_results: list[TradeResult] = []
        lock = threading.Lock()

        def _monitor_one(symbol: str, signal: SignalResult, state: PositionState) -> None:
            outcome = self._monitor.monitor(state)
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
    # Helpers
    # ------------------------------------------------------------------

    def _evaluate_strategies(self, symbol: str, df, today_df) -> SignalResult:
        """
        Run all strategies and return the first BUY signal found.
        If none fire, return the last NONE result.
        """
        last_result = _none_signal(symbol)
        for strategy in self._strategies:
            result = strategy.generate_signal(symbol, df, today_df)
            last_result = result
            if result.direction == Direction.BUY:
                return result
        return last_result

    def _get_current_price(self, symbol: str, df) -> float:
        """Use the last close price as a proxy for current price."""
        return float(df["close"].iloc[-1])


def _none_signal(symbol: str) -> SignalResult:
    from strategy.models import SignalResult
    return SignalResult(symbol=symbol, direction=Direction.NONE, reason="")
