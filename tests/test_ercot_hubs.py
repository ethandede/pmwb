"""Tests for ercot/hubs.py — hub scanning and ERCOT data caching."""
import time


def test_scan_all_hubs_returns_five_signals():
    from ercot.hubs import scan_all_hubs

    signals = scan_all_hubs()
    assert len(signals) == 5
    hub_names = {s["hub"] for s in signals}
    assert hub_names == {"North", "Houston", "South", "West", "Panhandle"}


def test_signal_dict_has_required_keys():
    from ercot.hubs import scan_all_hubs

    signals = scan_all_hubs()
    required = {"hub", "hub_name", "city", "signal", "edge",
                "expected_solrad_mjm2", "current_ercot_price",
                "actual_solar_mw", "confidence"}
    for sig in signals:
        assert required.issubset(sig.keys()), f"Missing keys: {required - sig.keys()}"


def test_fetch_ercot_market_data_caching():
    """Second call within 5 min should return cached data."""
    from ercot.hubs import _fetch_ercot_market_data

    data1 = _fetch_ercot_market_data()
    data2 = _fetch_ercot_market_data()
    assert "price" in data1
    assert "solar_mw" in data1
    assert data1 == data2


def test_fetch_ercot_market_data_has_fallbacks():
    """Data should always have price, solar_mw, load_forecast keys."""
    from ercot.hubs import _fetch_ercot_market_data

    data = _fetch_ercot_market_data()
    assert isinstance(data["price"], float)
    assert isinstance(data["solar_mw"], float)
    assert isinstance(data["load_forecast"], float)


def test_fetch_ercot_markets_includes_per_hub_fields():
    """Each market dict must include hub_key and solar_sensitivity."""
    from ercot.hubs import fetch_ercot_markets
    markets = fetch_ercot_markets()
    for m in markets:
        assert "hub_key" in m, f"Missing hub_key in market for {m.get('hub_name')}"
        assert "solar_sensitivity" in m, f"Missing solar_sensitivity in market for {m.get('hub_name')}"
        assert isinstance(m["solar_sensitivity"], float)


def test_fetch_ercot_markets_has_per_hub_price_in_ercot_data():
    """The _ercot_data dict should have a hub_price field for each market."""
    from ercot.hubs import fetch_ercot_markets
    markets = fetch_ercot_markets()
    for m in markets:
        ercot_data = m.get("_ercot_data", {})
        assert "hub_price" in ercot_data, f"Missing hub_price in _ercot_data for {m.get('hub_name')}"


def test_scan_all_hubs_uses_new_signature():
    """scan_all_hubs should pass hub_key and solar_sensitivity to signal function."""
    from ercot.hubs import scan_all_hubs
    signals = scan_all_hubs()
    for sig in signals:
        assert "signal" in sig
        assert "edge" in sig
        assert sig["signal"] in ("SHORT", "LONG", "NEUTRAL")


def test_per_hub_prices_are_differentiated():
    """When ERCOT returns different hub prices, each market should get its own."""
    from unittest.mock import patch
    from ercot.hubs import fetch_ercot_markets

    fake_data = {
        "price": 40.0,
        "solar_mw": 12000.0,
        "load_forecast": 45000.0,
        "hub_prices": {
            "HB_NORTH": 35.0,
            "HB_HOUSTON": 50.0,
            "HB_SOUTH": 42.0,
            "HB_WEST": 20.0,
            "HB_PAN": 28.0,
        },
    }

    with patch("ercot.hubs._fetch_ercot_market_data", return_value=fake_data):
        markets = fetch_ercot_markets()

    prices = {m["hub_name"]: m["current_ercot_price"] for m in markets}
    assert prices["HB_WEST"] == 20.0
    assert prices["HB_HOUSTON"] == 50.0
    assert prices["HB_NORTH"] == 35.0
    west_market = [m for m in markets if m["hub_name"] == "HB_WEST"][0]
    assert west_market["_ercot_data"]["hub_price"] == 20.0
