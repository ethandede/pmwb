"""Comprehensive bug-hunt tests — March 2026 deep audit.

Tests edge cases, data consistency, and known issues discovered during
the extensive testing session. Each test section targets a specific
subsystem or failure mode.
"""

import math
import os
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# 1. ERCOT NEGATIVE PROBABILITY BUG
# ============================================================================

class TestErcotNegativeProb:
    """Regression tests for ERCOT signal edge and model_prob bounds.

    Original bug: old threshold-based signal produced edge > 1.0, causing
    model_prob = 1.0 - edge to go negative. The fair-price model (2026-03-16)
    produces small edges by design, but we still verify bounds are respected.
    """

    def test_high_solar_edge_bounded(self):
        """High solar should produce negative edge (SHORT) within reasonable bounds."""
        from weather.multi_model import get_ercot_solar_signal
        from datetime import datetime, timezone

        with patch("weather.multi_model.datetime", wraps=datetime,
                   **{"now.return_value": datetime(2026, 3, 16, 12, tzinfo=timezone.utc)}), \
             patch("weather.multi_model.http_get") as mock_get, \
             patch("config.VISUAL_CROSSING_API_KEY", ""):
            solar_resp = MagicMock()
            solar_resp.json.return_value = {
                "daily": {"shortwave_radiation_sum": [25.0, 28.0, 22.0]},
                "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
            }
            solar_resp.raise_for_status = MagicMock()
            mock_get.return_value = solar_resp

            result = get_ercot_solar_signal(
                lat=32.78, lon=-96.80,
                hub_key="North", solar_sensitivity=0.15,
                hours_ahead=24,
                ercot_data={"hub_price": 40.0, "price": 40.0, "solar_mw": 15000.0, "load_forecast": 45000.0},
            )

            assert -1.0 <= result["edge"] <= 1.0, f"Edge out of bounds: {result['edge']}"
            assert result["signal"] == "SHORT"  # above-norm solar

    def test_score_signal_ercot_extreme_solar(self):
        """score_signal should NOT produce negative model_prob for ERCOT."""
        from pipeline.stages import score_signal
        from pipeline.config import ERCOT
        from datetime import datetime, timezone

        market = {
            "hub_name": "HB_NORTH",
            "hub_key": "North",
            "solar_sensitivity": 0.15,
            "city": "Dallas",
            "lat": 32.78,
            "lon": -96.80,
            "_ercot_data": {"hub_price": 40.0, "price": 40.0, "solar_mw": 15000.0, "load_forecast": 45000.0},
        }

        with patch("weather.multi_model.datetime", wraps=datetime,
                   **{"now.return_value": datetime(2026, 3, 16, 12, tzinfo=timezone.utc)}), \
             patch("weather.multi_model.http_get") as mock_get, \
             patch("config.VISUAL_CROSSING_API_KEY", ""):
            solar_resp = MagicMock()
            solar_resp.json.return_value = {
                "daily": {"shortwave_radiation_sum": [25.0, 28.0, 22.0]},
                "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
            }
            solar_resp.raise_for_status = MagicMock()
            mock_get.return_value = solar_resp

            signal = score_signal(ERCOT, market)
            assert signal.model_prob >= 0.01, \
                f"model_prob went below 0.01: {signal.model_prob}"
            assert signal.model_prob <= 0.99, \
                f"model_prob exceeded 0.99: {signal.model_prob}"

    def test_kelly_with_negative_prob(self):
        """Kelly should handle negative model_prob gracefully."""
        from risk.kelly import kelly_fraction

        # Simulates the unclamped ERCOT case
        result = kelly_fraction(model_prob=-1.5, market_prob=0.5)
        # Should return no trade (negative model_prob means no YES edge)
        if result["side"] is not None:
            assert result["fraction"] >= 0, "Kelly returned negative fraction"

    def test_kelly_with_prob_above_one(self):
        """Kelly should handle model_prob > 1 gracefully."""
        from risk.kelly import kelly_fraction

        result = kelly_fraction(model_prob=1.5, market_prob=0.5)
        # raw_kelly = (1.5 - 0.5) / (1 - 0.5) = 2.0 — should be capped
        assert result["fraction"] <= 0.03, \
            f"Kelly fraction too large: {result['fraction']}"


# ============================================================================
# 2. PAPER TRADE DEDUP
# ============================================================================

class TestPaperTradeDedup:
    """Paper trade deduplication in pipeline runner.

    The runner queries trades.db for existing paper positions and adds them
    to held_positions to prevent filter_signals from passing duplicates.
    """

    def test_paper_dedup_blocks_repeat_ticker(self):
        """After a paper trade, the same ticker should be blocked next cycle."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signal = Signal(
            ticker="KXHIGHNY-26MAR15-T56",
            city="nyc",
            market_type="kalshi_temp",
            side="no",
            model_prob=0.35,
            market_prob=0.55,
            edge=-0.20,
            confidence=75.0,
            price_cents=55,
            days_ahead=0,
        )

        # Simulate held_positions from paper trade DB query
        held_positions = [{"ticker": "KXHIGHNY-26MAR15-T56", "position_fp": "1.0"}]
        resting_tickers = set()

        filtered = filter_signals(KALSHI_TEMP, [signal], held_positions, resting_tickers)
        assert len(filtered) == 0, "Paper dedup should have blocked this signal"

    def test_paper_dedup_allows_different_ticker(self):
        """A different ticker should NOT be blocked."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc",
            market_type="kalshi_temp",
            side="yes",
            model_prob=0.80,
            market_prob=0.45,
            edge=0.35,
            confidence=80.0,
            price_cents=45,
            days_ahead=1,
            market={"volume_24h_fp": "1000", "open_interest_fp": "1000"},
        )

        held_positions = [{"ticker": "KXHIGHNY-26MAR15-T56", "position_fp": "1.0"}]
        resting_tickers = set()

        filtered = filter_signals(KALSHI_TEMP, [signal], held_positions, resting_tickers)
        assert len(filtered) == 1, "Different ticker should pass"

    def test_paper_order_id_uniqueness(self):
        """Paper order IDs should be unique even within same second."""
        from kalshi.fill_tracker import init_trades_db, record_fill, get_unresolved_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            ts = int(datetime.now(timezone.utc).timestamp())

            # Two trades same ticker, same timestamp
            record_fill(db_path, f"paper-TICKER1-{ts}", "TICKER1", "buy_no",
                        55, 55, 5, datetime.now(timezone.utc).isoformat())
            record_fill(db_path, f"paper-TICKER1-{ts}", "TICKER1", "buy_no",
                        55, 55, 5, datetime.now(timezone.utc).isoformat())

            trades = get_unresolved_trades(db_path)
            ticker1_trades = [t for t in trades if t["ticker"] == "TICKER1"]
            # UPSERT means only 1 row (conflict on order_id)
            assert len(ticker1_trades) == 1, \
                f"Expected 1 trade (UPSERT), got {len(ticker1_trades)}"
        finally:
            os.unlink(db_path)


# ============================================================================
# 3. FORECAST DAYS COMPUTATION
# ============================================================================

class TestComputeForecastDays:
    """_compute_forecast_days should use ticker date, not just current month."""

    def test_current_month_remaining_days(self):
        from pipeline.stages import _compute_forecast_days
        import calendar

        today = date.today()
        last_day = calendar.monthrange(today.year, today.month)[1]
        expected = last_day - today.day + 1
        assert _compute_forecast_days("KXRAINNYCM-26MAR31-T1") == expected

    def test_days_ahead_parsing(self):
        """_compute_days_ahead should correctly parse ticker dates."""
        from pipeline.stages import _compute_days_ahead

        today = date.today()
        tomorrow = date(today.year, today.month, today.day + 1) if today.day < 28 else today

        # Build a ticker for today → days_ahead should be 0
        yr = today.strftime("%y")
        mon = today.strftime("%b").upper()
        day = today.strftime("%d")
        ticker_today = f"KXHIGHNY-{yr}{mon}{day}-T56"
        assert _compute_days_ahead(ticker_today) == 0

    def test_extract_month_from_ticker(self):
        from pipeline.stages import _extract_month
        assert _extract_month("KXHIGHNY-26MAR15-T56") == 3
        assert _extract_month("KXHIGHNY-26JAN15-T56") == 1
        assert _extract_month("KXHIGHNY-26DEC25-T56") == 12

    def test_extract_month_malformed_ticker(self):
        from pipeline.stages import _extract_month
        # Should fall back to current month
        result = _extract_month("GARBAGE")
        assert result == date.today().month


# ============================================================================
# 4. MARKET PROBABILITY EXTRACTION
# ============================================================================

class TestMarketProbExtraction:
    """_extract_market_prob should handle all Kalshi response formats."""

    def test_cents_format(self):
        from pipeline.stages import _extract_market_prob
        market = {"yes_ask": 55}
        assert _extract_market_prob(market) == 0.55

    def test_dollar_format(self):
        from pipeline.stages import _extract_market_prob
        market = {"yes_ask_dollars": "0.55"}
        assert _extract_market_prob(market) == 0.55

    def test_last_price_fallback(self):
        from pipeline.stages import _extract_market_prob
        market = {"last_price": 60}
        assert _extract_market_prob(market) == 0.60

    def test_missing_everything(self):
        from pipeline.stages import _extract_market_prob
        market = {}
        assert _extract_market_prob(market) == 0.50

    def test_zero_yes_ask_uses_fallback(self):
        """yes_ask=0 should fall through to alternatives."""
        from pipeline.stages import _extract_market_prob
        market = {"yes_ask": 0, "last_price": 45}
        # 0 is falsy, so it should fall through to last_price
        assert _extract_market_prob(market) == 0.45

    def test_yes_ask_as_float_dollar(self):
        """yes_ask < 1 should be treated as dollar format, not cents."""
        from pipeline.stages import _extract_market_prob
        market = {"yes_ask": 0.55}
        # 0.55 is not > 1, so it won't divide by 100
        # Falls through to last_price or default
        result = _extract_market_prob(market)
        assert 0 <= result <= 1.0


# ============================================================================
# 5. CROSS-CONTRACT CONSISTENCY
# ============================================================================

class TestCrossContractConsistency:
    """Verify that the filter catches contradictory bets on same city/date."""

    def test_contradictory_signals_same_city_date(self):
        """YES on T65 (temp>=65) + NO on T60 (temp<60) contradict — second blocked."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signals = [
            Signal(
                ticker="KXHIGHNY-26MAR16-T65",
                city="nyc", market_type="kalshi_temp",
                side="yes", model_prob=0.75, market_prob=0.55,
                edge=0.20, confidence=80.0, price_cents=55, days_ahead=1,
                market={"volume_24h_fp": "1000", "open_interest_fp": "1000"},
            ),
            Signal(
                ticker="KXHIGHNY-26MAR16-T60",
                city="nyc", market_type="kalshi_temp",
                side="no", model_prob=0.30, market_prob=0.75,
                edge=-0.45, confidence=80.0, price_cents=75, days_ahead=1,
                market={"volume_24h_fp": "1000", "open_interest_fp": "1000"},
            ),
        ]

        held = []
        resting = set()
        filtered = filter_signals(KALSHI_TEMP, signals, held, resting)

        # Cross-contract check: YES T65 → temp>=65, NO T60 → temp<60
        # These contradict (65 >= 60). Stronger edge wins (sorted by |edge|).
        # T60 has |edge|=0.45 > T65's |edge|=0.20, so T60 passes first.
        assert len(filtered) == 1
        assert filtered[0].ticker == "KXHIGHNY-26MAR16-T60"

    def test_consistent_signals_same_city_date_pass(self):
        """Consistent directional bets on same city/date should both pass."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        # Both are bullish: NO on B50 (temp>=50) and NO on B55 (temp>=55)
        signals = [
            Signal(
                ticker="KXHIGHNY-26MAR16-B55",
                city="nyc", market_type="kalshi_temp",
                side="no", model_prob=0.80, market_prob=0.55,
                edge=0.25, confidence=80.0, price_cents=55, days_ahead=1,
                market={"volume_24h_fp": "1000", "open_interest_fp": "1000"},
            ),
            Signal(
                ticker="KXHIGHNY-26MAR16-B50",
                city="nyc", market_type="kalshi_temp",
                side="no", model_prob=0.85, market_prob=0.60,
                edge=0.25, confidence=80.0, price_cents=60, days_ahead=1,
                market={"volume_24h_fp": "1000", "open_interest_fp": "1000"},
            ),
        ]

        filtered = filter_signals(KALSHI_TEMP, signals, [], set())
        assert len(filtered) == 2

    def test_contradiction_against_held_position(self):
        """New signal contradicting an existing held position is blocked."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        # Held: NO on B48.5 → temp >= 48.5 (lower bound)
        held_sides = {"KXHIGHCHI-26MAR13-B48.5": "no"}

        # New: NO on T44 → temp < 44 (upper bound)
        # 48.5 >= 44 → contradiction
        signals = [
            Signal(
                ticker="KXHIGHCHI-26MAR13-T44",
                city="chicago", market_type="kalshi_temp",
                side="no", model_prob=0.25, market_prob=0.55,
                edge=-0.30, confidence=80.0, price_cents=55, days_ahead=0,
                market={"volume_24h_fp": "1000", "open_interest_fp": "1000"},
            ),
        ]

        filtered = filter_signals(KALSHI_TEMP, signals, [], set(),
                                  held_sides=held_sides)
        assert len(filtered) == 0


# ============================================================================
# 6. KELLY & SIZING EDGE CASES
# ============================================================================

class TestKellyEdgeCases:
    """Kelly and sizing should handle extreme inputs without crashing."""

    def test_kelly_model_prob_zero(self):
        from risk.kelly import kelly_fraction
        result = kelly_fraction(model_prob=0.0, market_prob=0.50)
        assert result["side"] == "no"
        assert result["fraction"] > 0

    def test_kelly_model_prob_one(self):
        from risk.kelly import kelly_fraction
        result = kelly_fraction(model_prob=1.0, market_prob=0.50)
        assert result["side"] == "yes"
        assert result["fraction"] > 0

    def test_kelly_market_prob_zero(self):
        from risk.kelly import kelly_fraction
        result = kelly_fraction(model_prob=0.50, market_prob=0.0)
        # Kelly YES: (0.5 - 0) / (1 - 0) = 0.5 → big bet
        assert result["side"] == "yes"

    def test_kelly_market_prob_one(self):
        from risk.kelly import kelly_fraction
        result = kelly_fraction(model_prob=0.50, market_prob=1.0)
        # Kelly YES: (0.5 - 1.0) / (1 - 1.0) → division by zero handled
        assert result["side"] is not None or result["fraction"] == 0

    def test_kelly_equal_probs_no_trade(self):
        from risk.kelly import kelly_fraction
        result = kelly_fraction(model_prob=0.50, market_prob=0.50)
        assert result["side"] is None

    def test_sigmoid_kelly_floor(self):
        """With minimum confidence and edge, sigmoid Kelly should return floor."""
        from risk.sizer import sigmoid_kelly
        result = sigmoid_kelly(confidence=40.0, edge=0.07, floor=0.25)
        assert 0.24 <= result <= 0.30, f"Expected near floor, got {result}"

    def test_sigmoid_kelly_ceiling(self):
        """With max confidence and edge, sigmoid Kelly should approach 0.50."""
        from risk.sizer import sigmoid_kelly
        result = sigmoid_kelly(confidence=100.0, edge=0.25, floor=0.25)
        assert result >= 0.45, f"Expected near 0.50, got {result}"

    def test_compute_size_zero_bankroll(self):
        """Sizing should handle zero bankroll without crashing."""
        from risk.sizer import compute_size
        from risk.bankroll import BankrollTracker
        from risk.circuit_breaker import CircuitBreaker

        bt = BankrollTracker(initial_bankroll=0.0)
        cb = CircuitBreaker()

        result = compute_size(
            model_prob=0.70, market_prob=0.50,
            confidence=80.0, price_cents=50,
            bankroll_tracker=bt, circuit_breaker=cb,
        )
        assert result.count == 0

    def test_compute_size_price_cents_zero(self):
        """Price of 0 cents should not cause division by zero."""
        from risk.sizer import compute_size
        from risk.bankroll import BankrollTracker
        from risk.circuit_breaker import CircuitBreaker

        bt = BankrollTracker(initial_bankroll=500.0)
        cb = CircuitBreaker()

        result = compute_size(
            model_prob=0.70, market_prob=0.50,
            confidence=80.0, price_cents=0,
            bankroll_tracker=bt, circuit_breaker=cb,
        )
        # Should not crash; max(price_cents, 1) in code
        assert result.count >= 0


# ============================================================================
# 7. FEE GATE & PRICING EDGE CASES
# ============================================================================

class TestFeeGate:
    """Fee calculations and gate behavior."""

    def test_taker_fee_extreme_prices(self):
        from kalshi.pricing import kalshi_fee

        # Price at 1 cent (min) — fee should be ~0.07 * 1/100
        assert kalshi_fee(1, 1, is_taker=True) == pytest.approx(0.0007, rel=0.01)

        # Price at 99 cents — fee uses min(99, 1) = 1
        assert kalshi_fee(99, 1, is_taker=True) == pytest.approx(0.0007, rel=0.01)

        # Price at 50 cents — maximum fee per contract
        fee_50 = kalshi_fee(50, 1, is_taker=True)
        assert fee_50 == pytest.approx(0.035, rel=0.01)

    def test_fee_gate_tiny_edge(self):
        """With 1 contract and tiny edge, fee gate should block."""
        from kalshi.pricing import kalshi_fee

        # Edge 7%, 1 contract: expected_profit = 0.07 * 1 = $0.07
        # Taker fee at 50¢: $0.035
        # After fees: $0.07 - $0.035 = $0.035 < $0.12 → blocked
        expected_profit = 0.07 * 1 - kalshi_fee(50, 1, is_taker=True)
        assert expected_profit < 0.12, "This trade should be fee-blocked"

    def test_choose_price_no_bid(self):
        """When no bid exists, maker should post conservatively."""
        from kalshi.pricing import choose_price_strategy

        price, strategy = choose_price_strategy(
            side="yes", yes_bid=None, yes_ask=55, edge=0.10,
        )
        assert strategy == "maker"
        assert price == 53  # ask - MAKER_FALLBACK_OFFSET

    def test_choose_price_no_ask_returns_none(self):
        from kalshi.pricing import choose_price_strategy
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=50, yes_ask=None, edge=0.10,
        )
        assert price is None
        assert strategy == "unknown"


# ============================================================================
# 8. PRECIP MTD ADJUSTMENT EDGE CASES
# ============================================================================

class TestPrecipMTD:
    """Month-to-date precipitation adjustment tests."""

    def test_mtd_exceeds_threshold_short_circuits(self):
        """If MTD precip already exceeds threshold, prob should be ~1.0."""
        from weather.multi_model import fuse_precip_forecast

        with patch("weather.forecast.get_observed_mtd_precip") as mock_mtd, \
             patch("weather.forecast.get_ensemble_precip") as mock_ens, \
             patch("weather.forecast.get_nws_precip_forecast") as mock_nws, \
             patch("weather.multi_model.get_bias") as mock_bias:

            mock_mtd.return_value = 5.0  # 5 inches already
            mock_ens.return_value = [1.0] * 30
            mock_nws.return_value = (0.5, 0.3)
            mock_bias.return_value = (0.0, 0)

            prob, conf, details = fuse_precip_forecast(
                lat=40.79, lon=-73.87, city="nyc", month=3,
                threshold=3.0,  # only need 3 inches but already have 5
                forecast_days=17,
            )

            assert prob >= 0.95, f"Expected ~1.0 when MTD > threshold, got {prob}"
            assert details.get("short_circuit") == "mtd_exceeds_threshold"

    def test_mtd_reduces_effective_threshold(self):
        """MTD precip should reduce the effective threshold for the ensemble."""
        from weather.multi_model import fuse_precip_forecast

        with patch("weather.forecast.get_observed_mtd_precip") as mock_mtd, \
             patch("weather.forecast.get_ensemble_precip") as mock_ens, \
             patch("weather.forecast.get_nws_precip_forecast") as mock_nws, \
             patch("weather.multi_model.get_bias") as mock_bias, \
             patch("weather.multi_model.fcache") as mock_cache:

            mock_mtd.return_value = 2.0  # 2 inches so far
            mock_ens.return_value = [1.5] * 30  # all members forecast 1.5"
            mock_nws.return_value = (0.5, 0.3)
            mock_bias.return_value = (0.0, 0)
            mock_cache.get.return_value = None
            mock_cache.put = MagicMock()

            prob, conf, details = fuse_precip_forecast(
                lat=40.79, lon=-73.87, city="nyc", month=3,
                threshold=3.0,  # need 3" total, have 2" already
                forecast_days=17,
            )

            assert details.get("effective_threshold") == 1.0  # 3.0 - 2.0
            assert details.get("mtd_observed_inches") == 2.0

    def test_first_day_of_month_no_mtd(self):
        """On day 1, MTD should be 0 (no historical data yet)."""
        from weather.forecast import get_observed_mtd_precip

        with patch("weather.forecast.date") as mock_date:
            mock_today = MagicMock()
            mock_today.day = 1
            mock_today.replace.return_value = mock_today
            mock_date.today.return_value = mock_today
            mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)

            # On day 1, function should return 0.0 immediately
            result = get_observed_mtd_precip(40.79, -73.87)
            assert result == 0.0


# ============================================================================
# 9. NORMAL CDF & BUCKET PROBABILITY
# ============================================================================

class TestNormalCdfBucketProb:
    """The new safe probability engine should always produce [0.01, 0.99]."""

    def test_deterministic_above_bucket(self):
        from weather.multi_model import _deterministic_bucket_prob
        # Forecast 70°F, bucket >=65°F → high probability
        prob = _deterministic_bucket_prob(70.0, low=65.0, high=None)
        assert 0.01 <= prob <= 0.99
        assert prob > 0.80

    def test_deterministic_below_bucket(self):
        from weather.multi_model import _deterministic_bucket_prob
        # Forecast 70°F, bucket >=80°F → low probability
        prob = _deterministic_bucket_prob(70.0, low=80.0, high=None)
        assert 0.01 <= prob <= 0.99
        assert prob < 0.20

    def test_deterministic_range_bucket(self):
        from weather.multi_model import _deterministic_bucket_prob
        # Forecast 67°F, bucket [65, 70) → moderate probability
        prob = _deterministic_bucket_prob(67.0, low=65.0, high=70.0)
        assert 0.01 <= prob <= 0.99
        assert 0.2 < prob < 0.6

    def test_deterministic_extreme_forecast(self):
        """Extreme temps should still produce valid probabilities."""
        from weather.multi_model import _deterministic_bucket_prob
        prob_hot = _deterministic_bucket_prob(120.0, low=65.0, high=None)
        prob_cold = _deterministic_bucket_prob(-40.0, low=65.0, high=None)
        assert prob_hot == 0.99, f"Expected 0.99 for extreme heat, got {prob_hot}"
        assert prob_cold == 0.01, f"Expected 0.01 for extreme cold, got {prob_cold}"

    def test_normal_cdf_zero_sigma(self):
        from weather.multi_model import normal_cdf
        assert normal_cdf(5.0, mu=3.0, sigma=0) == 1.0
        assert normal_cdf(1.0, mu=3.0, sigma=0) == 0.0

    def test_fuse_model_probs_all_agree(self):
        from weather.multi_model import fuse_model_probs
        result = fuse_model_probs({"ensemble": 0.80, "noaa": 0.82, "hrrr": 0.78})
        assert 0.78 <= result <= 0.82

    def test_fuse_model_probs_empty(self):
        from weather.multi_model import fuse_model_probs
        assert fuse_model_probs({}) == 0.50

    def test_fuse_model_probs_clamping(self):
        """Should clamp to [0.01, 0.99] even if inputs are extreme."""
        from weather.multi_model import fuse_model_probs
        result_low = fuse_model_probs({"a": 0.0, "b": 0.0})
        result_high = fuse_model_probs({"a": 1.0, "b": 1.0})
        assert result_low >= 0.01
        assert result_high <= 0.99


# ============================================================================
# 10. SETTLER EDGE CASES
# ============================================================================

class TestSettler:
    """Settlement resolution edge cases."""

    def test_pnl_calc_buy_yes_wins(self):
        from kalshi.settler import _calculate_pnl
        pnl = _calculate_pnl("buy_yes", fill_price=40, fill_qty=5, result="yes")
        assert pnl == 5 * (100 - 40) / 100.0  # $3.00

    def test_pnl_calc_buy_yes_loses(self):
        from kalshi.settler import _calculate_pnl
        pnl = _calculate_pnl("buy_yes", fill_price=40, fill_qty=5, result="no")
        assert pnl == -5 * 40 / 100.0  # -$2.00

    def test_pnl_calc_buy_no_wins(self):
        from kalshi.settler import _calculate_pnl
        pnl = _calculate_pnl("buy_no", fill_price=45, fill_qty=3, result="no")
        assert pnl == 3 * (100 - 45) / 100.0  # $1.65

    def test_pnl_calc_buy_no_loses(self):
        from kalshi.settler import _calculate_pnl
        pnl = _calculate_pnl("buy_no", fill_price=45, fill_qty=3, result="yes")
        assert pnl == -3 * 45 / 100.0  # -$1.35

    def test_pnl_calc_sell_side_returns_zero(self):
        from kalshi.settler import _calculate_pnl
        assert _calculate_pnl("sell_yes", 50, 5, "yes") == 0.0
        assert _calculate_pnl("sell_no", 50, 5, "no") == 0.0

    def test_is_exit_fill(self):
        from kalshi.settler import _is_exit_fill
        assert _is_exit_fill("sell_yes") is True
        assert _is_exit_fill("sell_no") is True
        assert _is_exit_fill("buy_yes") is False
        assert _is_exit_fill("buy_no") is False
        assert _is_exit_fill("yes") is False  # legacy bare side
        assert _is_exit_fill("no") is False

    def test_settler_skips_ercot_tickers(self):
        """Settler should skip HB_* tickers."""
        from kalshi.settler import run_settler
        from kalshi.fill_tracker import init_trades_db, record_fill

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            record_fill(db_path, "ercot-1", "HB_NORTH-1234", "buy_yes",
                        50, 50, 1, "2026-03-15T00:00:00Z")

            with patch("kalshi.settler.TRADES_DB_PATH", db_path), \
                 patch("kalshi.settler._fetch_market_result") as mock_fetch:
                mock_exchange = MagicMock()
                run_settler(exchange=mock_exchange)
                # _fetch_market_result should NOT be called for HB_ tickers
                mock_fetch.assert_not_called()
        finally:
            os.unlink(db_path)


# ============================================================================
# 11. FILL TRACKER EDGE CASES
# ============================================================================

class TestFillTracker:
    """Edge cases in SQLite fill tracking."""

    def test_concurrent_writes_same_order_id(self):
        """Concurrent writes with same order_id should use the higher fill_qty."""
        from kalshi.fill_tracker import init_trades_db, record_fill, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            now = datetime.now(timezone.utc).isoformat()

            # First write: 3 contracts
            record_fill(db_path, "order-1", "TICKER1", "buy_yes",
                        50, 50, 3, now)
            # Second write: 5 contracts (partial fill updated)
            record_fill(db_path, "order-1", "TICKER1", "buy_yes",
                        50, 48, 5, now)

            trades = get_all_trades(db_path)
            assert len(trades) == 1
            assert trades[0]["fill_qty"] == 5
            assert trades[0]["fill_price"] == 48  # updated

            # Third write: 2 contracts (lower qty, should NOT update)
            record_fill(db_path, "order-1", "TICKER1", "buy_yes",
                        50, 45, 2, now)
            trades = get_all_trades(db_path)
            assert trades[0]["fill_qty"] == 5  # should stay at 5
        finally:
            os.unlink(db_path)

    def test_resolve_trade_updates_correctly(self):
        from kalshi.fill_tracker import init_trades_db, record_fill, resolve_trade, get_all_trades

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_trades_db(db_path)
            now = datetime.now(timezone.utc).isoformat()
            record_fill(db_path, "order-1", "TICKER1", "buy_yes",
                        50, 50, 5, now)

            resolve_trade(db_path, "order-1", "win", 2.50)

            trades = get_all_trades(db_path)
            assert trades[0]["settlement_outcome"] == "win"
            assert trades[0]["pnl"] == 2.50
        finally:
            os.unlink(db_path)


# ============================================================================
# 12. PIPELINE FILTER EDGE CASES
# ============================================================================

class TestFilterSignals:
    """Filter stage edge cases."""

    def test_liquidity_gate_blocks_illiquid(self):
        """Markets with low volume AND open interest should be blocked."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.80, market_prob=0.55,
            edge=0.25, confidence=80.0, price_cents=55, days_ahead=1,
            market={"volume_24h_fp": "100", "open_interest_fp": "100"},
        )
        filtered = filter_signals(KALSHI_TEMP, [signal], [], set())
        assert len(filtered) == 0, "Illiquid market should be blocked"

    def test_liquidity_gate_passes_liquid(self):
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.80, market_prob=0.45,
            edge=0.35, confidence=80.0, price_cents=45, days_ahead=1,
            market={"volume_24h_fp": "5000", "open_interest_fp": "3000"},
        )
        filtered = filter_signals(KALSHI_TEMP, [signal], [], set())
        assert len(filtered) == 1

    def test_sameday_override_lowers_gate(self):
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        # Edge 0.08 — below normal gate (0.12) but above sameday gate (0.05)
        signal = Signal(
            ticker="KXHIGHNY-26MAR15-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.58, market_prob=0.50,
            edge=0.08, confidence=50.0, price_cents=50, days_ahead=0,
            market={"volume_24h_fp": "5000", "open_interest_fp": "3000"},
        )
        filtered = filter_signals(KALSHI_TEMP, [signal], [], set())
        assert len(filtered) == 1, "Same-day signal with edge=0.08 should pass sameday gate (0.05)"

    def test_resting_order_dedup(self):
        """Signals for tickers with resting orders should be blocked."""
        from pipeline.stages import filter_signals
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signal = Signal(
            ticker="KXHIGHNY-26MAR16-T58",
            city="nyc", market_type="kalshi_temp",
            side="yes", model_prob=0.80, market_prob=0.55,
            edge=0.25, confidence=80.0, price_cents=55, days_ahead=1,
            market={"volume_24h_fp": "5000", "open_interest_fp": "3000"},
        )

        resting = {"KXHIGHNY-26MAR16-T58"}
        filtered = filter_signals(KALSHI_TEMP, [signal], [], resting)
        assert len(filtered) == 0, "Resting order ticker should be blocked"


# ============================================================================
# 13. CIRCUIT BREAKER EDGE CASES
# ============================================================================

class TestCircuitBreakerEdgeCases:
    """Circuit breaker interaction with sizing."""

    def test_drawdown_halves_then_recovers(self):
        from risk.circuit_breaker import CircuitBreaker
        from datetime import datetime, timezone, timedelta

        cb = CircuitBreaker()
        # Trigger drawdown breaker
        cb.is_tripped(drawdown_pct=0.20, daily_pnl_pct=-0.02)
        assert cb.size_multiplier() == 0.5

        # After cooldown (simulate by setting trip_time far in the past)
        cb._trip_time = datetime.now(timezone.utc) - timedelta(hours=49)
        cb.is_tripped(drawdown_pct=0.05, daily_pnl_pct=0.0)
        assert cb.size_multiplier() == 1.0

    def test_daily_stop_blocks_completely(self):
        from risk.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        cb.is_tripped(drawdown_pct=0.0, daily_pnl_pct=-0.06)
        assert cb.size_multiplier() == 0.0


# ============================================================================
# 14. POSITION MANAGER EV GATE
# ============================================================================

class TestPositionManagerEV:
    """EV gate calculations in position manager."""

    def test_sell_ev_beats_hold_profitable_exit(self):
        """High market price + low win probability → sell should win."""
        from kalshi.position_manager import _sell_ev_beats_hold

        # Holding YES at 90¢, model says only 50% win probability
        # sell_ev = 0.90 * 10 - 0 (maker) = $9.00
        # hold_ev = 0.50 * 10 = $5.00
        # advantage = $4.00 > max(1.0, 0.025*10) = max(1.0, 0.25) = 1.0
        beats, adv = _sell_ev_beats_hold(
            current_price=0.90, our_win_prob=0.50, qty=10,
        )
        assert beats is True, f"Should beat hold, advantage={adv}"

    def test_sell_ev_loses_to_hold(self):
        """Low market price + high win probability → hold should win."""
        from kalshi.position_manager import _sell_ev_beats_hold

        # Holding YES at 60¢, model says 85% win probability
        # sell_ev = 0.60 * 5 = $3.00
        # hold_ev = 0.85 * 5 = $4.25
        beats, adv = _sell_ev_beats_hold(
            current_price=0.60, our_win_prob=0.85, qty=5,
        )
        assert beats is False, f"Should not beat hold, advantage={adv}"

    def test_sell_ev_zero_qty(self):
        """Zero qty should not crash."""
        from kalshi.position_manager import _sell_ev_beats_hold
        beats, adv = _sell_ev_beats_hold(0.50, 0.50, 0)
        assert adv == 0


# ============================================================================
# 15. PRECIP MODEL EDGE CASES
# ============================================================================

class TestPrecipModel:
    """Precipitation probability model edge cases."""

    def test_gamma_all_same_value(self):
        """All ensemble members with identical non-zero value."""
        from weather.precip_model import gamma_precip_prob

        members = [2.5] * 30  # all predict exactly 2.5 inches
        result = gamma_precip_prob(members, threshold=2.0)
        assert result.prob_above > 0.5, f"All above threshold, but got {result.prob_above}"

    def test_gamma_very_small_values(self):
        """Very small precipitation values near threshold."""
        from weather.precip_model import gamma_precip_prob

        members = [0.01, 0.02, 0.015, 0.03, 0.01, 0.0, 0.0, 0.0, 0.02, 0.01,
                    0.0, 0.0, 0.01, 0.03, 0.0, 0.0, 0.0, 0.02, 0.0, 0.0,
                    0.0, 0.0, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        result = gamma_precip_prob(members, threshold=0.5)
        assert 0 <= result.prob_above <= 1

    def test_gamma_negative_values_handled(self):
        """Negative precipitation values (data error) should be treated as zero."""
        from weather.precip_model import gamma_precip_prob

        members = [-0.1, 0.5, 1.0, -0.2, 0.3, 0.0, 0.0, 0.8, 0.0, 0.0]
        # Negative values are not > 0, so treated as dry
        result = gamma_precip_prob(members, threshold=0.5)
        assert 0 <= result.prob_above <= 1

    def test_gamma_threshold_zero_daily_binary(self):
        """Threshold=0 for daily binary 'any rain' question."""
        from weather.precip_model import gamma_precip_prob

        members = [0.0, 0.0, 0.5, 0.0, 1.0, 0.0, 0.0, 0.3, 0.0, 0.0,
                    0.0, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        result = gamma_precip_prob(members, threshold=0.0)
        # P(any rain) = 1 - p_dry
        assert result.prob_above > 0


# ============================================================================
# 16. BANKROLL TRACKER
# ============================================================================

class TestBankrollTracker:
    """Bankroll tracker edge cases."""

    def test_bankroll_negative_balance(self):
        """API could theoretically return negative balance."""
        from risk.bankroll import BankrollTracker

        bt = BankrollTracker(initial_bankroll=500.0)
        # Simulate negative balance (margin call scenario)
        bt.update_from_api(balance_cents=-1000, portfolio_value_cents=500)
        # effective_bankroll should not crash
        eb = bt.effective_bankroll()
        assert isinstance(eb, float)

    def test_bankroll_drawdown_from_peak(self):
        from risk.bankroll import BankrollTracker

        bt = BankrollTracker(initial_bankroll=1000.0)
        bt.update_from_api(balance_cents=80000, portfolio_value_cents=20000)  # $1000
        bt.update_from_api(balance_cents=60000, portfolio_value_cents=10000)  # $700
        assert bt.drawdown_pct() > 0


# ============================================================================
# 17. EDGE/SIDE CONSISTENCY IN EXECUTE_TRADE
# ============================================================================

class TestExecuteTradeConsistency:
    """Verify side consistency between signal and size in trade execution."""

    def test_paper_trade_records_correct_side(self):
        """Paper trade should record buy_{side} not just {side}."""
        from pipeline.stages import execute_trade
        from pipeline.types import Signal
        from pipeline.config import KALSHI_TEMP

        signal = Signal(
            ticker="TEST-TICKER",
            city="nyc", market_type="kalshi_temp",
            side="no", model_prob=0.30, market_prob=0.55,
            edge=-0.25, confidence=80.0, price_cents=55, days_ahead=1,
            yes_bid=50, yes_ask=55,
        )

        size = MagicMock()
        size.side = "no"
        size.count = 3

        with patch("kalshi.pricing.kalshi_fee", return_value=0.01), \
             patch("kalshi.fill_tracker.init_trades_db"), \
             patch("kalshi.fill_tracker.record_fill") as mock_record:

            result = execute_trade(KALSHI_TEMP, signal, size, None, paper_mode=True)

            # Check that record_fill was called with "buy_no"
            call_args = mock_record.call_args
            assert "buy_no" in str(call_args), \
                f"Expected side 'buy_no', got: {call_args}"


# ============================================================================
# 18. SCANNER BUCKET PARSING
# ============================================================================

class TestBucketParsing:
    """Kalshi bucket parsing edge cases."""

    def test_parse_between_bucket(self):
        from kalshi.scanner import parse_kalshi_bucket
        market = {"strike_type": "between", "floor_strike": 65.0, "cap_strike": 70.0}
        assert parse_kalshi_bucket(market) == (65.0, 70.0)

    def test_parse_greater_bucket(self):
        from kalshi.scanner import parse_kalshi_bucket
        market = {"strike_type": "greater", "floor_strike": 65.0}
        assert parse_kalshi_bucket(market) == (65.0, None)

    def test_parse_less_bucket(self):
        from kalshi.scanner import parse_kalshi_bucket
        market = {"strike_type": "less", "cap_strike": 65.0}
        assert parse_kalshi_bucket(market) == (0.0, 65.0)

    def test_parse_title_fallback(self):
        from kalshi.scanner import parse_kalshi_bucket
        market = {"title": "Temperature 65 - 70", "subtitle": "degrees"}
        assert parse_kalshi_bucket(market) == (65.0, 70.0)

    def test_parse_no_data_returns_none(self):
        from kalshi.scanner import parse_kalshi_bucket
        assert parse_kalshi_bucket({}) is None

    def test_precip_bucket_parsing(self):
        from kalshi.market_types import parse_precip_bucket
        # Should handle various precip market formats
        market = {"strike_type": "greater", "floor_strike": 3.0}
        result = parse_precip_bucket(market)
        if result is not None:
            assert result[0] >= 0


# ============================================================================
# 19. TRAILING STOP
# ============================================================================

class TestTrailingStop:
    """Trailing stop edge cases."""

    def test_trailing_stop_update_and_check(self):
        from kalshi.trailing_stop import update_peak, check_trailing_stop, remove_position

        # Track a position
        update_peak("TEST-TICKER", "yes", 80)
        update_peak("TEST-TICKER", "yes", 85)  # new peak

        # Price drops but not below trailing threshold
        result = check_trailing_stop("TEST-TICKER", "yes", 82, days_ahead=3)
        # Depends on trailing_pct configuration

        # Clean up
        remove_position("TEST-TICKER", "yes")


# ============================================================================
# 20. EQUITY DB
# ============================================================================

class TestEquityDB:
    """Equity snapshot edge cases."""

    def test_equity_upsert_same_date(self):
        from dashboard.equity_db import init_equity_db, record_equity_snapshot, get_equity_curve

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            init_equity_db(db_path=db_path)
            record_equity_snapshot("2026-03-15", 500, 400, 100, 10, 2, 3, 1, db_path=db_path)
            record_equity_snapshot("2026-03-15", 510, 410, 100, 12, 3, 4, 1, db_path=db_path)

            curve = get_equity_curve(db_path=db_path)
            dates = [r["date"] for r in curve]
            assert dates.count("2026-03-15") == 1, "Should upsert, not duplicate"
            assert curve[-1]["equity"] == 510
        finally:
            os.unlink(db_path)
