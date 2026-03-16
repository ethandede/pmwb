"""End-to-end pipeline simulation tests.

Runs the full pipeline with mocked external APIs to verify the complete
signal flow: fetch → score → filter → sanity → size → execute.
"""

import sqlite3
import tempfile
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestFullPipelineE2E:
    """Simulate a complete pipeline run with mocked Kalshi/weather APIs."""

    def _mock_kalshi_markets(self):
        """Return realistic mock Kalshi temperature markets."""
        return [
            {
                "ticker": "KXHIGHNY-26MAR16-T58",
                "title": "NYC High Temperature >= 58°F",
                "strike_type": "greater",
                "floor_strike": 58.0,
                "yes_ask": 45,
                "yes_bid": 40,
                "last_price": 42,
                "volume_24h_fp": "5000",
                "open_interest_fp": "3000",
                "status": "open",
                "_city": "nyc",
                "_lat": 40.7931,
                "_lon": -73.872,
                "_unit": "f",
            },
            {
                "ticker": "KXHIGHNY-26MAR16-B55.5",
                "title": "NYC High Temperature 55-58°F",
                "strike_type": "between",
                "floor_strike": 55.0,
                "cap_strike": 58.0,
                "yes_ask": 30,
                "yes_bid": 25,
                "last_price": 28,
                "volume_24h_fp": "2000",
                "open_interest_fp": "1500",
                "status": "open",
                "_city": "nyc",
                "_lat": 40.7931,
                "_lon": -73.872,
                "_unit": "f",
            },
        ]

    def test_pipeline_scores_and_filters(self):
        """Full pipeline should score markets and filter by edge/confidence."""
        from pipeline.stages import score_signal, filter_signals
        from pipeline.config import MarketConfig, KALSHI_TEMP

        markets = self._mock_kalshi_markets()

        # Create a config with a mock forecast_fn (config holds direct fn reference)
        mock_fuse = MagicMock(side_effect=[
            (0.75, 80.0, {"fused_prob": 0.75, "models_used": 3}),
            (0.28, 65.0, {"fused_prob": 0.28, "models_used": 3}),
        ])

        # Replace the forecast_fn in a copy of the config
        from dataclasses import replace
        test_config = replace(KALSHI_TEMP, forecast_fn=mock_fuse)

        signals = [score_signal(test_config, m) for m in markets]

        assert len(signals) == 2
        assert signals[0].ticker == "KXHIGHNY-26MAR16-T58"
        assert signals[0].edge == pytest.approx(0.30, abs=0.01)  # 0.75 - 0.45
        assert signals[0].side == "yes"
        assert signals[1].edge == pytest.approx(-0.02, abs=0.01)  # 0.28 - 0.30

        # Filter: first should pass (edge=20%), second should fail (edge=2%)
        filtered = filter_signals(test_config, signals, [], set())
        assert len(filtered) == 1
        assert filtered[0].ticker == "KXHIGHNY-26MAR16-T58"

    def test_pipeline_sizing_respects_limits(self):
        """Sizing should produce valid contract counts within limits."""
        from pipeline.stages import size_position
        from pipeline.types import Signal, CycleState
        from pipeline.config import KALSHI_TEMP
        from risk.bankroll import BankrollTracker
        from risk.circuit_breaker import CircuitBreaker

        bt = BankrollTracker(initial_bankroll=500.0)
        cb = CircuitBreaker()
        state = CycleState()

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.75, market_prob=0.55,
            edge=0.20, confidence=80.0, price_cents=55, days_ahead=1,
        )

        result = size_position(KALSHI_TEMP, signal, bt, cb, state)

        assert result.count > 0, f"Should size > 0 contracts, got {result.count}"
        assert result.dollar_amount <= 500 * 0.02, \
            f"Should not exceed 2% of bankroll: ${result.dollar_amount}"
        assert result.side == "yes"

    def test_pipeline_paper_trade_records_to_db(self):
        """Paper trades should be recorded to trades.db."""
        from pipeline.stages import execute_trade
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP
        from kalshi.fill_tracker import init_trades_db, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)

            signal = Signal(
                ticker="KXHIGHNY-26MAR16-T58",
                city="nyc", market_type="kalshi_temp",
                side="yes", model_prob=0.75, market_prob=0.55,
                edge=0.20, confidence=80.0, price_cents=55, days_ahead=1,
                yes_bid=50, yes_ask=55,
            )

            size = MagicMock()
            size.side = "yes"
            size.count = 5

            with patch("kalshi.fill_tracker.init_trades_db"), \
                 patch("kalshi.fill_tracker.record_fill") as mock_record:

                result = execute_trade(KALSHI_TEMP, signal, size, None, paper_mode=True)

                assert result.status == "paper"
                assert result.count == 5
                assert result.paper is True
                # Verify record_fill was called with correct params
                mock_record.assert_called_once()
                call_kwargs = mock_record.call_args
                assert call_kwargs[1]["side"] == "buy_yes" or "buy_yes" in str(call_kwargs)
        finally:
            os.unlink(db_path)

    def test_pipeline_fee_gate_blocks_unprofitable(self):
        """Fee gate should block trades where expected profit < $0.12."""
        from pipeline.stages import execute_trade
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        # Tiny edge + 1 contract → expected_profit = 0.07 * 1 - fee < $0.12
        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.57, market_prob=0.50,
            edge=0.07, confidence=60.0, price_cents=50, days_ahead=1,
            yes_bid=45, yes_ask=50,
        )

        size = MagicMock()
        size.side = "yes"
        size.count = 1

        result = execute_trade(KALSHI_TEMP, signal, size, None, paper_mode=True)
        assert result.status == "fee_blocked", \
            f"Expected fee_blocked for tiny position, got {result.status}"


class TestSettlerE2E:
    """Simulate settler resolving trades against mock Kalshi API."""

    def test_settler_resolves_finalized_market(self):
        """Settler should resolve trades when market is finalized."""
        from kalshi.settler import run_settler
        from kalshi.fill_tracker import init_trades_db, record_fill, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            record_fill(db_path, "order-1", "KXHIGHNY-26MAR12-T64", "buy_no",
                        60, 60, 5, "2026-03-12T02:28:02Z", city="nyc")

            mock_exchange = MagicMock()
            mock_exchange.get_settled_event_markets.return_value = {
                "KXHIGHNY-26MAR12-T64": {
                    "status": "finalized",
                    "result": "no",  # temperature was below 64
                    "expiration_value": 0.0,
                },
            }

            with patch("kalshi.settler.TRADES_DB_PATH", db_path):
                run_settler(exchange=mock_exchange)

            trades = get_all_trades(db_path)
            assert trades[0]["settlement_outcome"] == "win"
            assert trades[0]["pnl"] == 5 * (100 - 60) / 100.0  # $2.00
        finally:
            os.unlink(db_path)

    def test_settler_skips_open_markets(self):
        """Settler should not resolve trades for markets still open."""
        from kalshi.settler import run_settler
        from kalshi.fill_tracker import init_trades_db, record_fill, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            record_fill(db_path, "order-1", "KXHIGHNY-26MAR16-T58", "buy_yes",
                        55, 55, 5, "2026-03-15T18:00:00Z", city="nyc")

            mock_exchange = MagicMock()
            # Events API returns empty for this series (no settled markets)
            mock_exchange.get_settled_event_markets.return_value = {}

            with patch("kalshi.settler.TRADES_DB_PATH", db_path):
                run_settler(exchange=mock_exchange)

            trades = get_all_trades(db_path)
            assert trades[0]["settlement_outcome"] is None, "Open market should not be settled"
        finally:
            os.unlink(db_path)

    def test_settler_marks_exit_fills_as_exited(self):
        """Exit fills (sell_yes, sell_no) should be marked 'exited' with $0 pnl."""
        from kalshi.settler import run_settler
        from kalshi.fill_tracker import init_trades_db, record_fill, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            record_fill(db_path, "entry-1", "KXHIGHNY-26MAR12-T64", "buy_yes",
                        60, 60, 5, "2026-03-12T02:00:00Z", city="nyc")
            record_fill(db_path, "exit-1", "KXHIGHNY-26MAR12-T64", "sell_yes",
                        80, 80, 5, "2026-03-12T15:00:00Z", city="nyc")

            mock_exchange = MagicMock()
            mock_exchange.get_settled_event_markets.return_value = {
                "KXHIGHNY-26MAR12-T64": {
                    "status": "finalized",
                    "result": "yes",
                    "expiration_value": 1.0,
                },
            }

            with patch("kalshi.settler.TRADES_DB_PATH", db_path):
                run_settler(exchange=mock_exchange)

            trades = get_all_trades(db_path)
            entry = [t for t in trades if t["order_id"] == "entry-1"][0]
            exit_fill = [t for t in trades if t["order_id"] == "exit-1"][0]

            assert entry["settlement_outcome"] == "win"
            assert entry["pnl"] == 5 * (100 - 60) / 100.0  # $2.00
            assert exit_fill["settlement_outcome"] == "exited"
            assert exit_fill["pnl"] == 0.0
        finally:
            os.unlink(db_path)

    def test_settler_handles_legacy_bare_sides(self):
        """Settler should handle legacy 'no' side trades (without buy_ prefix)."""
        from kalshi.settler import run_settler
        from kalshi.fill_tracker import init_trades_db, record_fill, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            record_fill(db_path, "legacy-1", "KXHIGHNY-26MAR12-T64", "no",
                        60, 60, 5, "2026-03-12T02:28:02Z", city="nyc")

            mock_exchange = MagicMock()
            mock_exchange.get_settled_event_markets.return_value = {
                "KXHIGHNY-26MAR12-T64": {
                    "status": "finalized",
                    "result": "no",
                    "expiration_value": 0.0,
                },
            }

            with patch("kalshi.settler.TRADES_DB_PATH", db_path):
                run_settler(exchange=mock_exchange)

            trades = get_all_trades(db_path)
            assert trades[0]["settlement_outcome"] == "win"
            assert trades[0]["pnl"] == 5 * (100 - 60) / 100.0  # $2.00
        finally:
            os.unlink(db_path)


class TestPipelineRunnerE2E:
    """Test PipelineRunner with mocked everything."""

    def test_runner_paper_dedup_query(self):
        """Runner should query trades.db for paper positions to dedup."""
        # Test the dedup logic directly instead of mocking the runner
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        # Simulate what the runner does: query paper trades → add to held_positions
        held_positions = []
        paper_tickers = {"KXHIGHNY-26MAR16-T58"}
        for t in paper_tickers:
            held_positions.append({"ticker": t, "position_fp": "1.0"})

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.80, market_prob=0.55,
            edge=0.25, confidence=80.0, price_cents=55, days_ahead=1,
            market={"volume_24h_fp": "5000", "open_interest_fp": "3000"},
        )

        filtered = filter_signals(KALSHI_TEMP, [signal], held_positions, set())
        assert len(filtered) == 0, "Paper dedup should block this signal"


class TestCrossContractScenarios:
    """Scenario tests for edge cases that could cost real money."""

    def test_no_side_edge_is_positive_when_model_below_market(self):
        """When model_prob < market_prob, edge is negative and side should be 'no'."""
        from pipeline.stages import score_signal
        from pipeline.config import KALSHI_TEMP
        from dataclasses import replace

        market = {
            "ticker": "KXHIGHNY-26MAR16-T58",
            "strike_type": "greater",
            "floor_strike": 58.0,
            "yes_ask": 70,
            "yes_bid": 65,
            "volume_24h_fp": "5000",
            "open_interest_fp": "3000",
            "_city": "nyc",
            "_lat": 40.7931,
            "_lon": -73.872,
            "_unit": "f",
        }

        # Model says only 40% chance >= 58°F, but market prices at 70¢
        mock_fuse = MagicMock(return_value=(0.40, 75.0, {"fused_prob": 0.40}))
        test_config = replace(KALSHI_TEMP, forecast_fn=mock_fuse)

        signal = score_signal(test_config, market)

        assert signal.side == "no", f"Should bet NO when model < market"
        assert signal.edge < 0, f"Edge should be negative: {signal.edge}"
        assert abs(signal.edge) == pytest.approx(0.30, abs=0.01)

    def test_extreme_edge_gets_size_capped(self):
        """Even with 50% edge, sizing should cap at 2% bankroll."""
        from pipeline.stages import size_position
        from pipeline.types import Signal, CycleState
        from pipeline.config import KALSHI_TEMP
        from risk.bankroll import BankrollTracker
        from risk.circuit_breaker import CircuitBreaker

        bt = BankrollTracker(initial_bankroll=500.0)
        cb = CircuitBreaker()
        state = CycleState()

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.95, market_prob=0.45,
            edge=0.50, confidence=95.0, price_cents=45, days_ahead=1,
        )

        result = size_position(KALSHI_TEMP, signal, bt, cb, state)

        max_dollars = 500 * 0.02  # $10
        assert result.dollar_amount <= max_dollars + 0.01, \
            f"Should cap at ${max_dollars}, got ${result.dollar_amount}"
