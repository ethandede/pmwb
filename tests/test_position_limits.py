import pytest
from risk.position_limits import check_limits, LimitResult


def test_per_order_limit():
    """Order exceeding per-order cap gets clamped."""
    result = check_limits(
        order_dollars=50.0,
        bankroll=1000.0,
        scan_spent=0.0,
        city_day_spent=0.0,
        total_exposure=0.0,
    )
    # 3% of $1000 = $30 max per order
    assert result.allowed_dollars <= 30.0
    assert result.allowed_dollars > 0


def test_per_scan_limit():
    """Scan budget cap respected."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        scan_spent=95.0,  # 9.5% already spent, limit is 10%
    )
    # Only $5 remaining in scan budget (10% of 1000 = 100)
    assert result.allowed_dollars <= 5.0


def test_per_city_day_limit():
    """City/day exposure cap respected."""
    result = check_limits(
        order_dollars=40.0,
        bankroll=1000.0,
        city_day_spent=45.0,  # 4.5% already, limit is 5%
    )
    # Only $5 remaining (5% of 1000 = 50)
    assert result.allowed_dollars <= 5.0


def test_total_exposure_limit():
    """Total exposure cap respected."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        total_exposure=290.0,  # 29% already, limit is 30%
    )
    # Only $10 remaining (30% of 1000 = 300)
    assert result.allowed_dollars <= 10.0


def test_below_minimum():
    """When all limits collapse below $0.50, signal no-trade."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        scan_spent=99.8,  # only $0.20 remaining
    )
    assert result.allowed_dollars == 0.0
    assert result.blocked
    assert "scan" in result.reason.lower()


def test_all_limits_pass():
    """Fresh scan, no exposure -- full order allowed (up to per-order cap)."""
    result = check_limits(
        order_dollars=20.0,
        bankroll=1000.0,
        city_day_spent=0.0,
        total_exposure=0.0,
    )
    assert result.allowed_dollars == 20.0
    assert not result.blocked
