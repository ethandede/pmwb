"""Tests for pipeline stage functions. All use mocks — no API/forecast calls."""
from unittest.mock import MagicMock
from pipeline.types import Signal, CycleState
from pipeline.stages import (
    fetch_markets, score_signal, filter_signals,
    sanity_check, size_position, execute_trade,
)


def test_fetch_markets_calls_config_fn():
    """fetch_markets delegates to config.fetch_fn."""
    config = MagicMock()
    config.fetch_fn.return_value = [{"ticker": "T1"}, {"ticker": "T2"}]
    exchange = MagicMock()

    markets = fetch_markets(config, exchange)

    config.fetch_fn.assert_called_once()
    assert len(markets) == 2


def test_score_signal_temp():
    """score_signal creates a Signal with correct fields for temp market."""
    config = MagicMock()
    config.name = "kalshi_temp"
    config.forecast_fn.return_value = (0.30, 72.0, {})
    config.fusion_weights = {"ensemble": 0.40, "noaa": 0.35, "hrrr": 0.25}
    config.bucket_parser = MagicMock(return_value=(56.0, None))

    market = {
        "ticker": "KXHIGHNY-26MAR15-T56",
        "_city": "nyc",
        "yes_ask": 55,
        "yes_bid": 50,
        "_lat": 40.7, "_lon": -74.0,
    }

    signal = score_signal(config, market)

    assert isinstance(signal, Signal)
    assert signal.ticker == "KXHIGHNY-26MAR15-T56"
    assert signal.market_type == "kalshi_temp"
    assert signal.model_prob == 0.30
    assert signal.city == "nyc"


def test_score_signal_ercot():
    """score_signal handles ERCOT markets (no bucket parser, different forecast shape)."""
    config = MagicMock()
    config.name = "ercot"
    config.bucket_parser = None
    config.forecast_fn.return_value = {
        "signal": "SHORT", "edge": 1.69, "confidence": 70,
        "expected_solrad_mjm2": 21.8,
    }
    config.fusion_weights = None

    market = {
        "hub": "North", "hub_name": "HB_NORTH", "city": "Dallas",
        "lat": 32.78, "lon": -96.80,
        "current_ercot_price": 40.0, "_ercot_data": {},
    }

    signal = score_signal(config, market)

    assert signal.market_type == "ercot"
    assert signal.city == "Dallas"
    assert signal.side == "no"  # SHORT maps to selling


def _make_signal(**overrides) -> Signal:
    """Helper to create test signals with defaults."""
    defaults = dict(
        ticker="KXHIGHNY-26MAR15-T56", city="nyc", market_type="kalshi_temp",
        side="no", model_prob=0.30, market_prob=0.55, edge=-0.25,
        confidence=72.0, price_cents=55, days_ahead=0,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def test_filter_edge_gate():
    """Signals below edge gate are filtered out."""
    config = MagicMock()
    config.edge_gate = 0.12
    config.confidence_gate = 60
    config.sameday_overrides = None
    config.exchange = "kalshi"

    signals = [
        _make_signal(ticker="KXHIGHNY-26MAR15-T56", edge=0.15, confidence=70),   # passes
        _make_signal(ticker="KXHIGHNY-26MAR15-T50", edge=0.05, confidence=70),   # filtered (edge too low)
        _make_signal(ticker="KXHIGHNY-26MAR15-T52", edge=-0.20, confidence=70),  # passes (abs edge)
    ]

    filtered = filter_signals(config, signals, held_positions=[], resting_tickers=set())
    assert len(filtered) == 2


def test_filter_sameday_override():
    """Same-day signals use looser thresholds from config."""
    config = MagicMock()
    config.edge_gate = 0.12
    config.confidence_gate = 60
    config.sameday_overrides = {"edge": 0.05, "confidence": 45}
    config.exchange = "kalshi"

    signal = _make_signal(edge=0.08, confidence=50, days_ahead=0)
    filtered = filter_signals(config, [signal], held_positions=[], resting_tickers=set())
    assert len(filtered) == 1  # passes with sameday override


def test_filter_confidence_gate():
    """Signals below confidence gate are filtered out."""
    config = MagicMock()
    config.edge_gate = 0.05
    config.confidence_gate = 60
    config.sameday_overrides = None
    config.exchange = "kalshi"

    signal = _make_signal(edge=0.15, confidence=40)
    filtered = filter_signals(config, [signal], held_positions=[], resting_tickers=set())
    assert len(filtered) == 0


def test_filter_resting_order_dedup():
    """Signals for tickers with resting buy orders are filtered."""
    config = MagicMock()
    config.edge_gate = 0.05
    config.confidence_gate = 40
    config.sameday_overrides = None
    config.exchange = "kalshi"

    signal = _make_signal(ticker="KXHIGHNY-26MAR15-T56", edge=0.20, confidence=80)
    resting = {"KXHIGHNY-26MAR15-T56"}
    filtered = filter_signals(config, [signal], held_positions=[], resting_tickers=resting)
    assert len(filtered) == 0


# --- sanity_check tests ---

def test_sanity_check_none_passes():
    """When config.sanity_fn is None, signal always passes."""
    config = MagicMock()
    config.sanity_fn = None
    signal = _make_signal()
    assert sanity_check(config, signal) is True


def test_sanity_check_calls_fn():
    """When config.sanity_fn exists, it's called with the signal."""
    config = MagicMock()
    config.sanity_fn.return_value = False
    signal = _make_signal()
    assert sanity_check(config, signal) is False
    config.sanity_fn.assert_called_once_with(signal)


# --- size_position tests ---

def test_size_position_basic():
    """size_position returns a SizeResult with nonzero count for good signal."""
    from risk.bankroll import BankrollTracker
    from risk.circuit_breaker import CircuitBreaker

    config = MagicMock()
    config.scan_frac = 0.10
    config.kelly_floor = 0.25
    config.max_contracts_per_event = 10
    config.sameday_overrides = None

    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    state = CycleState()

    signal = _make_signal(model_prob=0.70, market_prob=0.55, edge=0.15,
                          confidence=85.0, price_cents=55)

    result = size_position(config, signal, bt, cb, state)
    assert result.count > 0
    assert result.dollar_amount > 0


def test_size_position_budget_exhausted():
    """size_position returns 0 when scan budget is exhausted."""
    from risk.bankroll import BankrollTracker
    from risk.circuit_breaker import CircuitBreaker

    config = MagicMock()
    config.scan_frac = 0.10
    config.kelly_floor = 0.25
    config.max_contracts_per_event = 10
    config.sameday_overrides = None

    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    state = CycleState()
    state.scan_spent = 999.0  # way over budget

    signal = _make_signal(model_prob=0.70, market_prob=0.55, edge=0.15,
                          confidence=85.0, price_cents=55)

    result = size_position(config, signal, bt, cb, state)
    assert result.count == 0


# --- execute_trade tests ---

def test_execute_trade_paper_mode():
    """In paper mode, execute_trade logs but doesn't call exchange."""
    config = MagicMock()
    config.pricing_fn = None
    config.exchange = "kalshi"
    exchange = MagicMock()

    signal = _make_signal()
    from risk.sizer import SizeResult
    size = SizeResult(side="no", count=5, dollar_amount=2.75,
                      raw_kelly=0.03, adjusted_kelly=0.02,
                      limit_reason="OK")

    result = execute_trade(config, signal, size, exchange, paper_mode=True)
    assert result.paper is True
    assert result.status == "paper"
    exchange.place_order.assert_not_called()
