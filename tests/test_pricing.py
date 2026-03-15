"""Tests for maker/taker pricing strategy and fee calculations."""
import pytest
from kalshi.pricing import kalshi_fee, choose_price_strategy


# --- Fee calculation ---

class TestKalshiFee:
    def test_taker_fee_at_50_cents(self):
        """Taker fee at 50c: 0.07 * min(50,50) / 100 * count."""
        fee = kalshi_fee(price_cents=50, count=1, is_taker=True)
        assert fee == pytest.approx(0.035)

    def test_taker_fee_at_50_cents_multiple(self):
        fee = kalshi_fee(price_cents=50, count=10, is_taker=True)
        assert fee == pytest.approx(0.35)

    def test_taker_fee_at_30_cents(self):
        """Fee symmetric: min(30, 70) = 30."""
        fee = kalshi_fee(price_cents=30, count=1, is_taker=True)
        assert fee == pytest.approx(0.021)

    def test_taker_fee_at_70_cents(self):
        """Same as 30c due to min(price, 100-price)."""
        fee = kalshi_fee(price_cents=70, count=1, is_taker=True)
        assert fee == pytest.approx(0.021)

    def test_taker_fee_at_5_cents(self):
        fee = kalshi_fee(price_cents=5, count=1, is_taker=True)
        assert fee == pytest.approx(0.0035)

    def test_maker_fee_is_zero(self):
        fee = kalshi_fee(price_cents=50, count=10, is_taker=False)
        assert fee == 0.0


# --- Price strategy ---

class TestChoosePriceStrategy:
    def test_maker_by_default_yes_side(self):
        """With moderate edge, should post as maker inside the spread."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=45, yes_ask=55, edge=0.10,
        )
        assert strategy == "maker"
        # Should improve the bid: bid + 1
        assert price == 46

    def test_taker_on_large_edge(self):
        """With large edge (>15%), cross the spread."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=45, yes_ask=55, edge=0.20,
        )
        assert strategy == "taker"
        assert price == 55  # hits the ask

    def test_maker_no_side(self):
        """NO side: our_ask = 100-yes_bid, our_bid = 100-yes_ask."""
        price, strategy = choose_price_strategy(
            side="no", yes_bid=45, yes_ask=55, edge=0.10,
        )
        assert strategy == "maker"
        # no_bid = 100-55 = 45, maker price = 45+1 = 46
        assert price == 46

    def test_taker_no_side(self):
        """NO side taker: should cross at no_ask = 100 - yes_bid."""
        price, strategy = choose_price_strategy(
            side="no", yes_bid=45, yes_ask=55, edge=0.20,
        )
        assert strategy == "taker"
        # no_ask = 100-45 = 55
        assert price == 55

    def test_sameday_lower_taker_threshold(self):
        """Same-day events should cross the spread at lower edge."""
        # 12% edge: not enough for multi-day taker, but enough for same-day
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=45, yes_ask=55, edge=0.12,
            is_same_day=True,
        )
        assert strategy == "taker"
        assert price == 55

    def test_sameday_still_maker_on_tiny_edge(self):
        """Same-day with small edge should still be maker."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=45, yes_ask=55, edge=0.06,
            is_same_day=True,
        )
        assert strategy == "maker"

    def test_no_bid_available(self):
        """When no bid exists, post at ask - 2 as maker."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=None, yes_ask=55, edge=0.10,
        )
        assert strategy == "maker"
        assert price == 53  # ask - 2

    def test_tight_spread_doesnt_cross(self):
        """With 1c spread, maker price should not cross the ask."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=54, yes_ask=55, edge=0.08,
        )
        assert strategy == "maker"
        # bid+1 = 55 would cross ask, so cap at ask-1 = 54
        assert price == 54

    def test_no_ask_returns_none(self):
        """If no ask data, can't determine strategy."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=45, yes_ask=None, edge=0.10,
        )
        assert price is None
        assert strategy == "unknown"

    def test_price_bounds(self):
        """Price should always be in [1, 99]."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=1, yes_ask=3, edge=0.05,
        )
        assert 1 <= price <= 99

    def test_edge_at_exact_threshold_is_taker(self):
        """Edge exactly at threshold should cross (>=, not >)."""
        price, strategy = choose_price_strategy(
            side="yes", yes_bid=45, yes_ask=55, edge=0.15,
        )
        assert strategy == "taker"
