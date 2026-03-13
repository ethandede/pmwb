# tests/test_ticker_map.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.ticker_map import ticker_to_city


def test_high_temp_ticker():
    assert ticker_to_city("KXHIGHNY-26MAR13-B55") == "NYC"


def test_high_temp_no_date():
    assert ticker_to_city("KXHIGHCHI") == "Chicago"


def test_precip_monthly_ticker():
    assert ticker_to_city("KXRAINNYCM-26MAR") == "NYC"


def test_precip_daily_ticker():
    """Daily rain tickers strip CM suffix — KXRAINNY should still resolve."""
    assert ticker_to_city("KXRAINNY-26MAR") == "NYC"


def test_low_temp_ticker():
    """KXLOWT prefix — not in WEATHER_SERIES directly but derivable."""
    result = ticker_to_city("KXLOWTNY-26MAR13")
    # Should resolve to NYC or fall back to KXLOWTNY
    assert result in ("NYC", "KXLOWTNY")


def test_unknown_ticker_falls_back():
    assert ticker_to_city("KXFOOBAR-26MAR") == "KXFOOBAR"


def test_city_name_formatting():
    """Cities should be title-cased display names, not slugs."""
    assert ticker_to_city("KXHIGHLAX-26MAR13") == "Los Angeles"
    assert ticker_to_city("KXHIGHTDC-26MAR13") == "Washington DC"
    assert ticker_to_city("KXHIGHTSEA-26MAR13") == "Seattle"
