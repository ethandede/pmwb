import pytest
from unittest.mock import patch, MagicMock


def test_execute_kalshi_signal_records_fill(tmp_path):
    """After a successful order, the fill should be recorded in trades.db."""
    db_path = str(tmp_path / "trades.db")

    mock_response = {"order": {"order_id": "test_ord_456", "status": "resting"}}

    with patch("kalshi.trader.place_order", return_value=mock_response), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.MAX_ORDER_USD", 2.0), \
         patch("config.MAX_SCAN_BUDGET", 10.0), \
         patch("config.HIGH_CONFIDENCE_MULTIPLIER", 1.5), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db, get_all_trades
        init_trades_db(db_path)

        from kalshi.trader import execute_kalshi_signal, reset_scan_budget
        reset_scan_budget()

        market = {"ticker": "KXHIGHNY-26MAR12-B65", "title": "NYC High Temp"}
        execute_kalshi_signal(market, "nyc", 0.70, 0.55, 0.15, "BUY YES", confidence=80)

        trades = get_all_trades(db_path)
        assert len(trades) == 1
        assert trades[0]["order_id"] == "test_ord_456"
        assert trades[0]["ticker"] == "KXHIGHNY-26MAR12-B65"
        assert trades[0]["side"] == "yes"
