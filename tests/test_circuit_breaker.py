import pytest
from datetime import datetime, timezone, timedelta
from risk.circuit_breaker import CircuitBreaker


def test_no_trip_normal_conditions():
    """Normal conditions -- not tripped."""
    cb = CircuitBreaker()
    assert not cb.is_tripped(drawdown_pct=0.05, daily_pnl_pct=-0.02)


def test_drawdown_trips_breaker():
    """15% drawdown from 7-day peak triggers breaker."""
    cb = CircuitBreaker(drawdown_threshold=0.15)
    assert cb.is_tripped(drawdown_pct=0.16, daily_pnl_pct=0.0)
    assert cb.size_multiplier() == 0.5  # halve sizes


def test_daily_stop_trips_breaker():
    """-5% daily P&L triggers daily stop."""
    cb = CircuitBreaker(daily_stop_pct=0.05)
    assert cb.is_tripped(drawdown_pct=0.0, daily_pnl_pct=-0.06)


def test_cooldown_period():
    """After tripping, breaker stays active for cooldown period."""
    cb = CircuitBreaker(cooldown_hours=48)
    cb.trip("drawdown")
    assert cb.is_in_cooldown()
    assert cb.size_multiplier() == 0.5


def test_cooldown_expires():
    """After cooldown period, breaker resets."""
    cb = CircuitBreaker(cooldown_hours=48)
    # Trip with a time in the past
    cb._trip_time = datetime.now(timezone.utc) - timedelta(hours=49)
    cb._tripped = True
    assert not cb.is_in_cooldown()
    assert cb.size_multiplier() == 1.0


def test_daily_stop_full_stop():
    """Daily stop should return 0.0 multiplier (full stop), not 0.5."""
    cb = CircuitBreaker()
    cb.trip("daily_stop")
    assert cb.size_multiplier() == 0.0


def test_drawdown_halves_not_stops():
    """Drawdown breaker halves sizes (0.5x), does not fully stop."""
    cb = CircuitBreaker()
    cb.trip("drawdown")
    assert cb.size_multiplier() == 0.5
