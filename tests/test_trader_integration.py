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
        trader_mod._scan_spent_temp = 0.0
        trader_mod._scan_spent_precip = 0.0
        trader_mod._bankroll_tracker._cash = 1000.0  # Set known bankroll
        trader_mod._bankroll_tracker._peak = 1000.0

        market = {"ticker": "KXHIGHNY-26MAR12-B65", "title": "NYC High Temp"}
        trader_mod.execute_kalshi_signal(market, "nyc", 0.70, 0.55, 0.15, "BUY YES", confidence=80)

        trades = get_all_trades(db_path)
        assert len(trades) == 1
        assert trades[0]["order_id"] == "test_ord_456"
        assert trades[0]["ticker"] == "KXHIGHNY-26MAR12-B65"
        assert trades[0]["side"] == "buy_yes"


def test_maker_pricing_used_when_bid_ask_available(tmp_path):
    """When market has bid/ask, should use maker strategy for moderate edge."""
    db_path = str(tmp_path / "trades.db")
    mock_response = {"order": {"order_id": "maker_ord_1", "status": "resting"}}

    captured_orders = []
    def capture_order(ticker, side, price_cents, count):
        captured_orders.append({"price_cents": price_cents, "side": side})
        return mock_response

    with patch("kalshi.trader.place_order", side_effect=capture_order), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.FRACTIONAL_KELLY", 0.25), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db
        init_trades_db(db_path)

        import kalshi.trader as trader_mod
        trader_mod._scan_spent_temp = 0.0
        trader_mod._scan_spent_precip = 0.0
        trader_mod._resting_buy_tickers = set()
        trader_mod._bankroll_tracker._cash = 1000.0
        trader_mod._bankroll_tracker._peak = 1000.0

        # Market with bid=45, ask=55, edge=10% → should use maker pricing
        market = {
            "ticker": "KXHIGHNY-26MAR15-B65",
            "title": "NYC High Temp",
            "yes_bid": 45,
            "yes_ask": 55,
        }
        trader_mod.execute_kalshi_signal(
            market, "nyc", 0.65, 0.55, 0.10, "BUY YES", confidence=80
        )

        assert len(captured_orders) == 1
        # Maker price should be bid+1 = 46, NOT the old formula which would give
        # int((0.55 + 0.10 * 0.3) * 100) = 58 (crossing the ask!)
        assert captured_orders[0]["price_cents"] == 46


def test_taker_pricing_on_large_edge(tmp_path):
    """When edge is large (>=15%), should cross the spread as taker."""
    db_path = str(tmp_path / "trades.db")
    mock_response = {"order": {"order_id": "taker_ord_1", "status": "resting"}}

    captured_orders = []
    def capture_order(ticker, side, price_cents, count):
        captured_orders.append({"price_cents": price_cents})
        return mock_response

    with patch("kalshi.trader.place_order", side_effect=capture_order), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.FRACTIONAL_KELLY", 0.25), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db
        init_trades_db(db_path)

        import kalshi.trader as trader_mod
        trader_mod._scan_spent_temp = 0.0
        trader_mod._scan_spent_precip = 0.0
        trader_mod._resting_buy_tickers = set()
        trader_mod._bankroll_tracker._cash = 1000.0
        trader_mod._bankroll_tracker._peak = 1000.0

        # Market with bid=45, ask=55, edge=20% → should cross as taker
        market = {
            "ticker": "KXHIGHNY-26MAR15-B65",
            "title": "NYC High Temp",
            "yes_bid": 45,
            "yes_ask": 55,
        }
        trader_mod.execute_kalshi_signal(
            market, "nyc", 0.75, 0.55, 0.20, "BUY YES", confidence=80
        )

        assert len(captured_orders) == 1
        # Taker should cross at the ask
        assert captured_orders[0]["price_cents"] == 55


def test_fallback_pricing_when_no_bid_ask(tmp_path):
    """When market has no bid/ask data, should fall back to old pricing."""
    db_path = str(tmp_path / "trades.db")
    mock_response = {"order": {"order_id": "fallback_ord_1", "status": "resting"}}

    captured_orders = []
    def capture_order(ticker, side, price_cents, count):
        captured_orders.append({"price_cents": price_cents})
        return mock_response

    with patch("kalshi.trader.place_order", side_effect=capture_order), \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.FRACTIONAL_KELLY", 0.25), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db
        init_trades_db(db_path)

        import kalshi.trader as trader_mod
        trader_mod._scan_spent_temp = 0.0
        trader_mod._scan_spent_precip = 0.0
        trader_mod._resting_buy_tickers = set()
        trader_mod._bankroll_tracker._cash = 1000.0
        trader_mod._bankroll_tracker._peak = 1000.0

        # Market with NO bid/ask — old-style data
        market = {
            "ticker": "KXHIGHNY-26MAR15-B65",
            "title": "NYC High Temp",
        }
        trader_mod.execute_kalshi_signal(
            market, "nyc", 0.70, 0.55, 0.15, "BUY YES", confidence=80
        )

        assert len(captured_orders) == 1
        # Should still work — falls back to legacy pricing
        assert 1 <= captured_orders[0]["price_cents"] <= 99


def test_fee_estimate_uses_real_kalshi_formula(tmp_path):
    """Fee gate should use real Kalshi taker/maker fee, not flat estimate."""
    db_path = str(tmp_path / "trades.db")

    with patch("kalshi.trader.place_order") as mock_place, \
         patch("kalshi.trader.TRADES_DB_PATH", db_path), \
         patch("config.PAPER_MODE", False), \
         patch("config.FRACTIONAL_KELLY", 0.25), \
         patch("alerts.telegram_alert.send_signal_alert"):

        from kalshi.fill_tracker import init_trades_db
        init_trades_db(db_path)

        import kalshi.trader as trader_mod
        trader_mod._scan_spent_temp = 0.0
        trader_mod._scan_spent_precip = 0.0
        trader_mod._resting_buy_tickers = set()
        trader_mod._bankroll_tracker._cash = 1000.0
        trader_mod._bankroll_tracker._peak = 1000.0

        # With maker pricing (edge=8%), fee should be $0 not $0.035+
        # This means a trade that was previously skipped due to fee gate
        # should now go through because maker fee = 0
        market = {
            "ticker": "KXHIGHNY-26MAR15-B65",
            "title": "NYC High Temp",
            "yes_bid": 45,
            "yes_ask": 55,
        }
        mock_place.return_value = {"order": {"order_id": "fee_ord_1", "status": "resting"}}

        # Small edge that would have been killed by old fee estimate
        # but should pass with maker fee = 0
        trader_mod.execute_kalshi_signal(
            market, "nyc", 0.62, 0.55, 0.07, "BUY YES", confidence=75
        )

        # With real maker fees ($0), trade should go through
        assert mock_place.called


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
