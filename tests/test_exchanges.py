"""Tests for exchange adapters. Uses mocks — no real API calls."""
from unittest.mock import patch, MagicMock
from exchanges.kalshi import KalshiExchange
from exchanges.ercot import ErcotExchange


def test_kalshi_get_balance():
    """get_balance returns cash + portfolio from API."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"balance": 17729, "portfolio_value": 4449}
        bal = exchange.get_balance()
    assert bal["balance"] == 17729
    assert bal["portfolio_value"] == 4449


def test_kalshi_get_positions():
    """get_positions returns market_positions list."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"market_positions": [{"ticker": "T1"}, {"ticker": "T2"}]}
        positions = exchange.get_positions()
    assert len(positions) == 2
    assert positions[0]["ticker"] == "T1"


def test_kalshi_get_orders():
    """get_orders returns orders list filtered by status."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"orders": [{"ticker": "T1", "status": "resting"}]}
        orders = exchange.get_orders(status="resting")
    assert len(orders) == 1


def test_kalshi_get_market():
    """get_market returns single market dict for settler."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_get') as mock_get:
        mock_get.return_value = {"market": {"ticker": "T1", "status": "finalized", "result": "yes"}}
        market = exchange.get_market("T1")
    assert market["status"] == "finalized"
    assert market["result"] == "yes"


def test_kalshi_place_order():
    """place_order sends POST with correct body."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_post_order') as mock_post:
        mock_post.return_value = {"order": {"order_id": "abc", "status": "executed"}}
        result = exchange.place_order("T1", "buy", "no", 55, 5)
    mock_post.assert_called_once_with("T1", "buy", "no", 55, 5)
    assert result["order"]["status"] == "executed"


def test_kalshi_sell_order():
    """sell_order delegates to _post_order with action='sell'."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_post_order') as mock_post:
        mock_post.return_value = {"order": {"order_id": "def", "status": "executed"}}
        result = exchange.sell_order("T1", "yes", 90, 3)
    mock_post.assert_called_once_with("T1", "sell", "yes", 90, 3)
    assert result["order"]["status"] == "executed"


def test_kalshi_cancel_order():
    """cancel_order sends DELETE with order_id in path."""
    exchange = KalshiExchange()
    with patch.object(exchange, '_delete') as mock_delete:
        mock_delete.return_value = {"order": {"order_id": "abc123", "status": "cancelled"}}
        result = exchange.cancel_order("abc123")
    mock_delete.assert_called_once_with("/trade-api/v2/portfolio/orders/abc123")
    assert result["order"]["status"] == "cancelled"


def test_ercot_fetch_market_data():
    """fetch_market_data returns price + solar + load."""
    exchange = ErcotExchange()
    with patch('exchanges.ercot._fetch_ercot_market_data') as mock_fetch:
        mock_fetch.return_value = {"price": 42.0, "solar_mw": 13000.0, "load_forecast": 51000.0}
        data = exchange.fetch_market_data()
    assert data["price"] == 42.0
    assert data["solar_mw"] == 13000.0


def test_ercot_get_positions():
    """get_positions returns open paper positions."""
    exchange = ErcotExchange()
    with patch('ercot.paper_trader.get_open_positions') as mock_pos:
        mock_pos.return_value = [{"hub": "North", "signal": "SHORT"}]
        positions = exchange.get_positions()
    assert len(positions) == 1
    assert positions[0]["hub"] == "North"
