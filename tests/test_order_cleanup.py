"""Tests for stale maker order cleanup."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from kalshi.order_cleanup import cleanup_stale_orders


def _make_order(order_id, ticker, created_time, action="buy"):
    """Helper to build a mock resting order dict."""
    return {
        "order_id": order_id,
        "ticker": ticker,
        "action": action,
        "side": "yes",
        "remaining_count_fp": "5",
        "created_time": created_time.isoformat(),
    }


class TestStaleOrderCleanup:
    def test_cancels_old_order(self):
        """Orders older than max_age_hours should be cancelled."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=7)
        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("old-1", "KXHIGHNY-26MAR20-T58", old_time),
        ]
        exchange.cancel_order.return_value = {"order": {"status": "cancelled"}}

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6)

        assert len(result) == 1
        assert result[0]["order_id"] == "old-1"
        assert result[0]["reason"] == "age"
        exchange.cancel_order.assert_called_once_with("old-1")

    def test_cancels_near_close_order(self):
        """Orders for events closing within close_proximity_hours should be cancelled."""
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%y%b%d").upper()
        ticker = f"KXHIGHNY-{today_str}-T58"

        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("close-1", ticker, now - timedelta(hours=1)),
        ]
        exchange.cancel_order.return_value = {"order": {"status": "cancelled"}}

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6, close_proximity_hours=24)

        assert len(result) == 1
        assert result[0]["reason"] == "event_close"

    def test_keeps_fresh_order(self):
        """Recent order for a distant event should not be cancelled."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=5)
        future_str = future.strftime("%y%b%d").upper()
        ticker = f"KXHIGHNY-{future_str}-T58"

        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("fresh-1", ticker, now - timedelta(hours=2)),
        ]

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6)

        assert len(result) == 0
        exchange.cancel_order.assert_not_called()

    def test_cancel_failure_continues(self):
        """If one cancel fails, continue processing remaining orders."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=7)
        exchange = MagicMock()
        exchange.get_orders.return_value = [
            _make_order("fail-1", "KXHIGHNY-26MAR20-T58", old_time),
            _make_order("ok-1", "KXHIGHCHI-26MAR20-T58", old_time),
        ]
        exchange.cancel_order.side_effect = [Exception("API error"), {"order": {"status": "cancelled"}}]

        with patch("kalshi.order_cleanup._send_cleanup_alert"):
            result = cleanup_stale_orders(exchange, max_age_hours=6)

        assert len(result) == 1
        assert result[0]["order_id"] == "ok-1"
        assert exchange.cancel_order.call_count == 2
