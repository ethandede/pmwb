import pytest
from unittest.mock import patch, MagicMock


def test_execute_kalshi_signal_records_fill(tmp_path):
    """After a successful order, the fill should be recorded in trades.db."""
    db_path = str(tmp_path / "trades.db")

    mock_response = {"order": {"order_id": "test_ord_456", "status": "resting"}}

    with patch("kalshi.trader.place_order", return_value=mock_response), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.FRACTIONAL_KELLY", 0.25), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db, get_all_trades
        init_trades_db(db_path)

        import kalshi.trader as trader_mod
        trader_mod._scan_spent = 0.0
        trader_mod._bankroll_tracker._cash = 1000.0  # Set known bankroll
        trader_mod._bankroll_tracker._peak = 1000.0

        market = {"ticker": "KXHIGHNY-26MAR12-B65", "title": "NYC High Temp"}
        trader_mod.execute_kalshi_signal(market, "nyc", 0.70, 0.55, 0.15, "BUY YES", confidence=80)

        trades = get_all_trades(db_path)
        assert len(trades) == 1
        assert trades[0]["order_id"] == "test_ord_456"
        assert trades[0]["ticker"] == "KXHIGHNY-26MAR12-B65"
        assert trades[0]["side"] == "yes"


def test_execute_kalshi_signal_kelly_sizing():
    """Kelly sizing should produce reasonable contract counts."""
    from risk.bankroll import BankrollTracker
    from risk.circuit_breaker import CircuitBreaker
    from risk.sizer import compute_size

    bt = BankrollTracker(initial_bankroll=1000.0)
    cb = CircuitBreaker()

    result = compute_size(
        model_prob=0.70, market_prob=0.55,
        confidence=85.0, price_cents=55,
        bankroll_tracker=bt, circuit_breaker=cb,
        fractional_kelly=0.25,
    )

    # Should size based on Kelly, not flat $2
    assert result.count > 0
    assert result.side == "yes"
    # At 0.25x Kelly with 85% conf: ~7% * $1000 = ~$70.80 → capped at 3% = $30
    assert result.dollar_amount <= 30.0
