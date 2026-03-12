import pytest
from unittest.mock import patch, MagicMock
from risk.bankroll import BankrollTracker


def test_bankroll_from_manual():
    """Manual bankroll when API is unavailable."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    assert bt.effective_bankroll() == 1000.0


def test_bankroll_from_api_response():
    """Bankroll from mocked API balance response."""
    bt = BankrollTracker(initial_bankroll=500.0)
    # Simulate Kalshi balance response (values in cents)
    bt.update_from_api(balance_cents=75000, portfolio_value_cents=25000)
    # $750 cash + $250 positions = $1000
    assert bt.effective_bankroll() == 1000.0


def test_bankroll_tracks_peak():
    """7-day rolling peak should update."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    bt.update_from_api(balance_cents=120000, portfolio_value_cents=0)
    assert bt.peak_bankroll() == 1200.0
    # Drop -- peak stays
    bt.update_from_api(balance_cents=90000, portfolio_value_cents=0)
    assert bt.peak_bankroll() == 1200.0


def test_bankroll_drawdown_pct():
    """Drawdown should be calculated from peak."""
    bt = BankrollTracker(initial_bankroll=1000.0)
    bt.update_from_api(balance_cents=120000, portfolio_value_cents=0)
    bt.update_from_api(balance_cents=102000, portfolio_value_cents=0)
    # Peak = 1200, current = 1020, drawdown = 180/1200 = 15%
    assert bt.drawdown_pct() == pytest.approx(0.15)
