import pytest
from kalshi.market_types import MarketType, detect_market_type, parse_precip_bucket


def test_detect_high_temp():
    assert detect_market_type("KXHIGHNY-26MAR12-B65") == MarketType.HIGH_TEMP


def test_detect_low_temp():
    assert detect_market_type("KXLOWTNYC-26MAR12-B30") == MarketType.LOW_TEMP


def test_detect_precip():
    assert detect_market_type("kxrainchim-26mar-4") == MarketType.PRECIP


def test_detect_snow():
    assert detect_market_type("kxnycsnowm-26mar-3") == MarketType.SNOW


def test_parse_precip_monthly_from_strike():
    """Monthly precip market with strike data → (threshold, None)."""
    market = {"ticker": "kxrainchim-26mar-4", "strike_type": "greater", "floor_strike": 4.0}
    result = parse_precip_bucket(market)
    assert result == (4.0, None)


def test_parse_precip_from_ticker_regex():
    """Parse threshold from ticker when no strike data."""
    market = {"ticker": "kxrainchim-26mar-4.5", "title": "Rain above 4.5 inches"}
    result = parse_precip_bucket(market)
    assert result is not None
    assert result[0] == pytest.approx(4.5)


def test_parse_precip_from_title():
    """Parse threshold from title fallback."""
    market = {"ticker": "kxrainchim-26mar", "title": "Rain in Chicago this month above 3 inches"}
    result = parse_precip_bucket(market)
    assert result is not None
    assert result[0] == pytest.approx(3.0)


def test_parse_precip_daily_binary():
    """Daily binary rain market → (0.0, None)."""
    market = {"ticker": "kxrainnyc-26mar11", "title": "Will it rain in NYC today?",
              "strike_type": "greater", "floor_strike": 0.0}
    result = parse_precip_bucket(market)
    assert result == (0.0, None)


def test_parse_precip_no_match():
    """No parseable threshold → None."""
    market = {"ticker": "unknown-market", "title": "Something unrelated"}
    result = parse_precip_bucket(market)
    assert result is None
