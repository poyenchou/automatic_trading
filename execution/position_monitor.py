"""
PositionMonitor — polls an open position until it is closed.

After OrderManager places the entry + TP/SL bracket orders, PositionMonitor
takes over and watches the position until one of three things happens:

  1. Take-profit fills  — Alpaca closes the position automatically via the
                          limit order; we detect this and log the win.
  2. Stop-loss triggers — Alpaca closes the position automatically via the
                          stop order; we detect this and log the loss.
  3. Bracket orders disappear but position is still open (edge case) —
                          we close the position manually at market.

The monitor does NOT compute indicators. Exit logic is purely based on
position state returned by the broker.
"""

import time
from datetime import datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import structlog

from broker.client import AlpacaClient
from broker.exceptions import BrokerError
from execution.models import PositionState

log = structlog.get_logger(__name__)

ET = ZoneInfo("America/New_York")


class PositionMonitor:
    def __init__(
        self,
        client: AlpacaClient,
        poll_interval_seconds: int = 30,
        exit_time_et: dt_time | None = None,
        timeout_seconds: int = 14400,
    ) -> None:
        self._client       = client
        self._interval     = poll_interval_seconds
        self._exit_time_et = exit_time_et   # wall-clock ET cutoff (production)
        self._timeout      = timeout_seconds # duration fallback (tests)

    def monitor(self, state: PositionState) -> str:
        """
        Block until the position in `state` is fully closed.

        Polls Alpaca every `poll_interval_seconds` seconds. Returns a string
        describing how the position was closed: "tp", "sl", "manual", or "timeout".

        Args:
            state: PositionState returned by OrderManager.execute().

        Returns:
            "tp"      — take-profit limit order filled
            "sl"      — stop-loss order triggered
            "manual"  — position closed manually (bracket orders missing)
            "timeout" — exit_time_et reached; position force-closed at market
        """
        if self._exit_time_et is not None:
            now_et = datetime.now(ET)
            exit_dt = datetime.combine(now_et.date(), self._exit_time_et, tzinfo=ET)
            deadline = time.monotonic() + max(0.0, (exit_dt - now_et).total_seconds())
        else:
            deadline = time.monotonic() + self._timeout

        log.info(
            "position_monitor.start",
            symbol=state.symbol,
            qty=state.qty,
            entry=state.entry_price,
            stop=state.stop_price,
            tp=state.take_profit_price,
            exit_time_et=str(self._exit_time_et) if self._exit_time_et else None,
        )

        while time.monotonic() < deadline:
            time.sleep(min(self._interval, max(0.0, deadline - time.monotonic())))

            # ── Check if position is still open ───────────────────────────
            position = self._client.get_position(state.symbol)

            if position is None:
                # Position is gone — one of the bracket orders fired
                outcome = self._determine_outcome(state)
                # Cancel whichever bracket order did NOT fill to avoid
                # an orphaned order attempting to sell shares we no longer own
                if outcome == "tp":
                    self._cancel_order(state.stop_order_id)
                else:
                    self._cancel_order(state.tp_order_id)
                log.info(
                    "position_monitor.closed",
                    symbol=state.symbol,
                    outcome=outcome,
                )
                return outcome

            # ── Position still open — refresh current price ───────────────
            current_price = position.current_price
            unrealized_pl = position.unrealized_pl
            log.debug(
                "position_monitor.poll",
                symbol=state.symbol,
                current_price=current_price,
                unrealized_pl=unrealized_pl,
            )

            # ── Safety check: bracket orders still alive? ─────────────────
            # If both bracket orders disappeared but position is still open,
            # close manually to prevent an unprotected position.
            sl_alive = self._order_is_open(state.stop_order_id)
            tp_alive = self._order_is_open(state.tp_order_id)

            if not sl_alive and not tp_alive:
                log.warning(
                    "position_monitor.bracket_orders_missing",
                    symbol=state.symbol,
                    stop_order_id=state.stop_order_id,
                    tp_order_id=state.tp_order_id,
                )
                self._close_manually(state.symbol)
                return "manual"

        # Timeout exceeded — force-close to avoid holding overnight
        log.error(
            "position_monitor.timeout",
            symbol=state.symbol,
            timeout_seconds=self._timeout,
        )
        self._cancel_order(state.stop_order_id)
        self._cancel_order(state.tp_order_id)
        self._close_manually(state.symbol)
        return "timeout"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_outcome(self, state: PositionState) -> str:
        """
        After the position closes, check which bracket order filled to
        determine if it was a win (TP) or loss (SL).
        """
        try:
            tp_order = self._client.get_order(state.tp_order_id)
            if tp_order.status == "filled":
                return "tp"
        except BrokerError:
            pass

        try:
            sl_order = self._client.get_order(state.stop_order_id)
            if sl_order.status == "filled":
                return "sl"
        except BrokerError:
            pass

        # Couldn't determine from order status — default to sl (conservative)
        return "sl"

    def _order_is_open(self, order_id: str) -> bool:
        """Return True if the order exists and is still open (not filled/canceled)."""
        try:
            order = self._client.get_order(order_id)
            return order.status not in ("filled", "canceled", "expired", "rejected")
        except BrokerError:
            return False

    def _cancel_order(self, order_id: str) -> None:
        """Cancel a bracket order, ignoring errors if it is already gone."""
        try:
            self._client.cancel_order(order_id)
            log.info("position_monitor.bracket_cancelled", order_id=order_id)
        except BrokerError as exc:
            # Already filled, canceled, or expired — nothing to do
            log.debug("position_monitor.bracket_cancel_skipped", order_id=order_id, reason=str(exc))

    def _close_manually(self, symbol: str) -> None:
        """Close a position at market as a safety fallback."""
        try:
            log.warning("position_monitor.closing_manually", symbol=symbol)
            self._client.close_position(symbol)
        except BrokerError as exc:
            log.error("position_monitor.manual_close_failed", symbol=symbol, error=str(exc))
