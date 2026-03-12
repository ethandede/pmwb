import pytest
from risk.sizer import compute_size, SizeResult
from risk.bankroll import BankrollTracker
from risk.circuit_breaker import CircuitBreaker


def test_compute_size_basic():
    """Basic sizing: Kelly → limited → contract count."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    assert result.count > 0
    assert result.side == "yes"
    assert result.dollar_amount > 0
    assert result.dollar_amount <= 30.0  # 3% of $1000


def test_compute_size_no_bet():
    """No edge → no trade."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.55, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    assert result.count == 0
    assert result.side is None


def test_compute_size_drawdown_breaker_halves():
    """Drawdown circuit breaker → half the normal size."""
    # Tripped tracker: 16.7% drawdown
    bt_tripped = BankrollTracker(initial_bankroll=100.0)
    bt_tripped.update_from_api(balance_cents=12000, portfolio_value_cents=0)
    bt_tripped.update_from_api(balance_cents=10000, portfolio_value_cents=0)
    cb = CircuitBreaker(drawdown_threshold=0.15)

    result_tripped = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=90,
        bankroll_tracker=bt_tripped, circuit_breaker=cb,
    )

    # Normal tracker: no drawdown
    bt_normal = BankrollTracker(initial_bankroll=100.0)
    cb2 = CircuitBreaker()

    result_normal = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=90,
        bankroll_tracker=bt_normal, circuit_breaker=cb2,
    )

    # Tripped halves kelly_dollars, so dollar_amount should be lower
    assert result_tripped.dollar_amount < result_normal.dollar_amount


def test_compute_size_daily_stop_blocks():
    """Daily stop → zero contracts (full stop, not halving)."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    bt.record_daily_pnl(-60.0)  # -6% of bankroll

    cb = CircuitBreaker(daily_stop_pct=0.05)
    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    assert result.count == 0
    assert "daily stop" in result.limit_reason


def test_compute_size_event_cap():
    """Per-event 2-contract cap limits output."""
    bt = BankrollTracker(initial_bankroll=10000.0)  # large bankroll
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.80, market_prob=0.55,
        confidence=95.0, price_cents=10,  # cheap contracts
        bankroll_tracker=bt, circuit_breaker=cb,
        event_contracts=1,  # already 1 contract on this event
    )
    # Should cap at 1 more contract (max 2 per event)
    assert result.count <= 1


def test_compute_size_respects_scan_budget():
    """Scan budget limits size."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
        scan_spent=95.0,  # 9.5% spent of 10% limit
    )
    # Only $5 remaining in scan budget
    assert result.dollar_amount <= 5.0


def test_compute_size_returns_contract_count():
    """Dollar amount → correct contract count at given price."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()
    result = compute_size(
        model_prob=0.80, market_prob=0.55,
        confidence=90.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
    )
    # count = dollar_amount * 100 / price_cents
    expected_count = int(result.dollar_amount * 100 / 55)
    assert result.count == expected_count
