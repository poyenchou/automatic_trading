"""
Tests for MorningWorkflow.
All broker calls and external dependencies are mocked — no network required.

Strategy: MorningWorkflow creates its collaborators (_screener, _fetcher,
_order_manager, _monitor) in __init__, so we mock them directly on the
instance after construction rather than patching the classes.

The scan loop calls _prime_window_open() to decide whether to keep scanning.
Tests patch this method to return True once then False, so the loop runs
exactly one iteration without any real sleeping.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from broker.exceptions import BrokerError
from broker.models import AccountInfo, OrderResponse
from config.settings import Settings
from execution.models import PositionState
from market_data.models import ScreenerResult
from orchestration.morning_workflow import MorningWorkflow
from strategy.models import Direction, SignalResult


# ── Helpers ──────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    base = dict(
        alpaca_api_key="k",
        alpaca_api_secret="s",
        paper_trading=True,
        risk_per_trade_pct=0.01,
        stop_loss_cents=0.10,
        max_shares=1000,
        max_concurrent_positions=2,
        min_stock_price=2.0,
        poll_interval_seconds=0,
        scan_interval_seconds=0,
        gap_min_pct=0.10,
        snapshot_batch_size=100,
    )
    base.update(overrides)
    return Settings(**base)


def _make_bars(price: float = 10.0, n: int = 50) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with a UTC DatetimeIndex."""
    idx = pd.date_range("2026-01-02 14:30", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": 1_000_000},
        index=idx,
    )


def _screener_result(symbol: str) -> ScreenerResult:
    return ScreenerResult(symbol=symbol, volume=5_000_000)


def _position_state(symbol: str = "X") -> PositionState:
    return PositionState(
        symbol=symbol,
        qty=100.0,
        entry_price=10.0,
        current_price=10.0,
        unrealized_pl=0.0,
        stop_price=9.90,
        take_profit_price=10.20,
        stop_order_id="stop-1",
        tp_order_id="tp-1",
    )


def _buy_signal(symbol: str) -> SignalResult:
    return SignalResult(symbol=symbol, direction=Direction.BUY, reason="test buy")


def _none_signal(symbol: str) -> SignalResult:
    return SignalResult(symbol=symbol, direction=Direction.NONE, reason="test none")


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.get_account.return_value = AccountInfo(
        id="acc1", status="ACTIVE", equity=100_000.0, buying_power=100_000.0,
    )
    return client


def _make_workflow(
    movers: list[ScreenerResult] | None = None,
    bars: pd.DataFrame | None = None,
    strategies=None,
    float_fetcher=None,
    monitor_outcome: str = "tp",
    settings: Settings | None = None,
) -> MorningWorkflow:
    """
    Build a MorningWorkflow with all internal collaborators mocked.
    _prime_window_open() is patched to run the loop exactly once.
    _sleep_until_next_scan() is patched to return 0 (no real sleeping).
    """
    wf = MorningWorkflow(
        client=_mock_client(),
        settings=settings or _settings(),
        strategies=strategies or [],
        float_fetcher=float_fetcher,
    )

    # Mock screener
    wf._screener = MagicMock()
    wf._screener.get_gappers.return_value = movers or []

    # Mock history fetcher — _fetch_bars() calls fetch_bars() internally
    wf._fetcher = MagicMock()
    wf._fetcher.fetch_bars.return_value = bars if bars is not None else _make_bars()

    # Mock order manager
    wf._order_manager = MagicMock()
    wf._order_manager.build_order_request.return_value = MagicMock()
    wf._order_manager.execute.return_value = _position_state()

    # Mock position monitor
    wf._monitor = MagicMock()
    wf._monitor.monitor.return_value = monitor_outcome

    # Patch loop control: run exactly one scan iteration, no sleeping
    wf._prime_window_open = MagicMock(side_effect=[True, False])
    wf._sleep_until_next_scan = MagicMock(return_value=0)

    return wf


# ── No movers / no signals ────────────────────────────────────────────────────

class TestNoMovers:
    def test_returns_empty_list_when_no_movers(self):
        wf = _make_workflow(movers=[])
        results = wf.run()
        assert results == []

    def test_returns_skipped_when_no_buy_signals(self):
        strategy = MagicMock()
        strategy.generate_signal.return_value = _none_signal("AAA")
        wf = _make_workflow(
            movers=[_screener_result("AAA")],
            strategies=[strategy],
        )
        results = wf.run()
        assert len(results) == 1
        assert results[0].symbol == "AAA"
        assert results[0].outcome == "skipped"


# ── Float filter ──────────────────────────────────────────────────────────────

class TestFloatFilter:
    def test_high_float_symbol_is_skipped(self):
        float_fetcher = MagicMock()
        float_fetcher.get_float_shares.return_value = 50_000_000  # above 20M threshold
        wf = _make_workflow(
            movers=[_screener_result("HIGH")],
            float_fetcher=float_fetcher,
        )
        results = wf.run()
        assert any(r.symbol == "HIGH" and r.outcome == "skipped" for r in results)

    def test_low_float_symbol_proceeds_to_signal(self):
        float_fetcher = MagicMock()
        float_fetcher.get_float_shares.return_value = 10_000_000  # below 20M threshold
        strategy = MagicMock()
        strategy.generate_signal.return_value = _none_signal("LOW")
        wf = _make_workflow(
            movers=[_screener_result("LOW")],
            strategies=[strategy],
            float_fetcher=float_fetcher,
        )
        wf.run()
        strategy.generate_signal.assert_called_once()

    def test_no_float_fetcher_skips_float_check(self):
        strategy = MagicMock()
        strategy.generate_signal.return_value = _none_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[strategy],
            float_fetcher=None,
        )
        wf.run()
        strategy.generate_signal.assert_called_once()

    def test_unknown_float_symbol_is_skipped(self):
        float_fetcher = MagicMock()
        float_fetcher.get_float_shares.return_value = None  # yfinance returned no data
        wf = _make_workflow(
            movers=[_screener_result("UNK")],
            float_fetcher=float_fetcher,
        )
        results = wf.run()
        assert any(r.symbol == "UNK" and r.outcome == "skipped" for r in results)


# ── Signal evaluation ─────────────────────────────────────────────────────────

class TestSignalEvaluation:
    def test_first_buy_signal_short_circuits_remaining_strategies(self):
        s1 = MagicMock()
        s1.generate_signal.return_value = _buy_signal("X")
        s2 = MagicMock()
        s2.generate_signal.return_value = _buy_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[s1, s2],
            monitor_outcome="tp",
        )
        wf.run()
        s1.generate_signal.assert_called_once()
        s2.generate_signal.assert_not_called()

    def test_none_from_first_strategy_falls_through_to_second(self):
        s1 = MagicMock()
        s1.generate_signal.return_value = _none_signal("X")
        s2 = MagicMock()
        s2.generate_signal.return_value = _buy_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[s1, s2],
            monitor_outcome="tp",
        )
        wf.run()
        s1.generate_signal.assert_called_once()
        s2.generate_signal.assert_called_once()


# ── Scan loop rescan ──────────────────────────────────────────────────────────

class TestScanLoop:
    def test_rescans_until_signal_fires(self):
        """Strategy returns NONE on first scan, BUY on second — both scans happen."""
        strategy = MagicMock()
        strategy.generate_signal.side_effect = [_none_signal("X"), _buy_signal("X")]
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[strategy],
            monitor_outcome="tp",
        )
        # Allow two scan iterations
        wf._prime_window_open = MagicMock(side_effect=[True, True, False])
        results = wf.run()
        assert strategy.generate_signal.call_count == 2
        assert any(r.outcome == "tp" for r in results)

    def test_already_traded_symbol_not_rescanned(self):
        """After a BUY and order placement, the symbol is not re-evaluated next scan."""
        strategy = MagicMock()
        strategy.generate_signal.return_value = _buy_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[strategy],
            monitor_outcome="tp",
            settings=_settings(max_concurrent_positions=2),
        )
        # Two iterations available
        wf._prime_window_open = MagicMock(side_effect=[True, True, False])
        wf.run()
        # Should only be called once even though two iterations ran
        assert strategy.generate_signal.call_count == 1


# ── Max concurrent positions ──────────────────────────────────────────────────

class TestMaxPositions:
    def test_stops_scanning_after_max_positions_reached(self):
        """With max_concurrent_positions=1, second mover is never evaluated."""
        strategy = MagicMock()
        strategy.generate_signal.return_value = _buy_signal("AAA")
        wf = _make_workflow(
            movers=[_screener_result("AAA"), _screener_result("BBB")],
            strategies=[strategy],
            settings=_settings(max_concurrent_positions=1),
            monitor_outcome="tp",
        )
        wf.run()
        assert strategy.generate_signal.call_count == 1


# ── Trade results ─────────────────────────────────────────────────────────────

class TestTradeResults:
    def test_outcome_tp_when_monitor_returns_tp(self):
        strategy = MagicMock()
        strategy.generate_signal.return_value = _buy_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[strategy],
            monitor_outcome="tp",
        )
        results = wf.run()
        trade = next(r for r in results if r.outcome != "skipped")
        assert trade.outcome == "tp"

    def test_outcome_sl_when_monitor_returns_sl(self):
        strategy = MagicMock()
        strategy.generate_signal.return_value = _buy_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[strategy],
            monitor_outcome="sl",
        )
        results = wf.run()
        trade = next(r for r in results if r.outcome != "skipped")
        assert trade.outcome == "sl"

    def test_order_failure_recorded_as_skipped(self):
        strategy = MagicMock()
        strategy.generate_signal.return_value = _buy_signal("X")
        wf = _make_workflow(
            movers=[_screener_result("X")],
            strategies=[strategy],
        )
        wf._order_manager.execute.side_effect = BrokerError("rejected")
        results = wf.run()
        assert any(r.symbol == "X" and r.outcome == "skipped" for r in results)

    def test_multiple_symbols_all_appear_in_results(self):
        strategy = MagicMock()
        strategy.generate_signal.side_effect = [_none_signal("AAA"), _none_signal("BBB")]
        wf = _make_workflow(
            movers=[_screener_result("AAA"), _screener_result("BBB")],
            strategies=[strategy],
        )
        results = wf.run()
        symbols = {r.symbol for r in results}
        assert "AAA" in symbols
        assert "BBB" in symbols
